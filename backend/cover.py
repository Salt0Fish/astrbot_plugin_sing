"""翻唱后端：整首歌 + 副歌片段。

流程：搜歌 → 下载 → 转 wav → demucs 分离 → RVC 换嗓 → 混音导出 mp3 → 截副歌 wav

用法：
    cover.py <关键词> <整首输出.mp3> <副歌输出.wav> [f0up] [index_rate] [protect]

环境变量：
    FFMPEG_PATH   ffmpeg 可执行文件（默认 PATH 里的 ffmpeg）
    RVC_MODEL     RVC 模型 .pth
    RVC_INDEX     RVC 索引 .index
    WORK_DIR      中间文件目录（默认本文件旁的 work/）
    CLIP_SECONDS  副歌长度（默认 45）
"""

import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
FF = os.environ.get("FFMPEG_PATH", "ffmpeg")
WORK = Path(os.environ.get("WORK_DIR", HERE / "work"))
CLIP = int(os.environ.get("CLIP_SECONDS", "45"))
MODEL = os.environ.get("RVC_MODEL", str(HERE / "models" / "model.pth"))
INDEX = os.environ.get("RVC_INDEX", str(HERE / "models" / "model.index"))

sys.path.insert(0, str(HERE))
from sources import fetch  # noqa: E402


def dur_of(path: Path) -> float:
    p = subprocess.run([FF, "-i", str(path)], capture_output=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)",
                  (p.stderr or b"").decode("utf-8", "replace"))
    if not m:
        return 0.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def pick_clip_start(total: float) -> float:
    if total <= 0:
        return 0.0
    if total > 200:
        return 55.0
    if total > 120:
        return total * 0.30
    return total * 0.25


def main(kw: str, out_full: Path, out_clip: Path,
         f0up: int = 0, index_rate: float = 0.75, protect: float = 0.33) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    got = fetch(kw, WORK)
    if not got.get("ok"):
        print("NOT_FOUND", got.get("reason", ""), flush=True)
        return
    src = got["path"]
    total = float(got.get("dur") or 0)
    print(f"选中: {got['title']} - {got['artist']} [{got['source']}] {total:.0f}s", flush=True)

    wav_full = WORK / "src_full.wav"
    subprocess.run([FF, "-y", "-i", str(src), "-ar", "44100", "-ac", "2", str(wav_full)],
                   capture_output=True)
    if not wav_full.exists():
        print("DECODE_FAIL", flush=True)
        return

    sep = WORK / "sep_full"
    if sep.exists():
        for p in sep.rglob("*.wav"):
            p.unlink()
    print("分离整首人声...", flush=True)
    subprocess.run([sys.executable, "-m", "demucs", "--two-stems=vocals",
                    "-n", "htdemucs", "--out", str(sep), str(wav_full)], capture_output=True)
    voc = next(sep.rglob("vocals.wav"), None)
    ins = next(sep.rglob("no_vocals.wav"), None)
    if not voc or not ins:
        print("SEP_FAIL", flush=True)
        return
    print("分离完成", flush=True)

    from rvc_python.infer import RVCInference

    rvc = RVCInference(device="cuda:0")
    rvc.load_model(MODEL, index_path=INDEX)
    rvc.set_params(f0method="rmvpe", f0up_key=f0up, index_rate=index_rate,
                   protect=protect, rms_mix_rate=0.25, resample_sr=0)
    conv = WORK / "conv_full.wav"
    print("整首换嗓中...", flush=True)
    rvc.infer_file(str(voc), str(conv))
    print("换嗓完成", flush=True)

    out_full.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FF, "-y", "-i", str(conv), "-i", str(ins), "-filter_complex",
         "[0:a]volume=1.0[v];[1:a]volume=1.0[i];[v][i]amix=inputs=2:duration=longest:"
         "dropout_transition=0,loudnorm=I=-16:TP=-1.5:LRA=11[out]",
         "-map", "[out]", "-ar", "44100", "-b:a", "192k", str(out_full)],
        capture_output=True)
    if not out_full.exists():
        print("MIX_FAIL", flush=True)
        return
    print(f"整首输出: {out_full}", flush=True)

    start = pick_clip_start(total)
    out_clip.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, "-y", "-ss", str(start), "-t", str(CLIP), "-i", str(out_full),
                    "-ar", "32000", "-ac", "1", str(out_clip)], capture_output=True)
    if out_clip.exists():
        print(f"副歌输出: {out_clip} ({dur_of(out_clip):.0f}s)", flush=True)
        print("DONE", flush=True)
    else:
        print("CLIP_FAIL", flush=True)


if __name__ == "__main__":
    kw = sys.argv[1]
    full = Path(sys.argv[2])
    clip = Path(sys.argv[3])
    f0 = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    ir = float(sys.argv[5]) if len(sys.argv) > 5 else 0.75
    pr = float(sys.argv[6]) if len(sys.argv) > 6 else 0.33
    main(kw, full, clip, f0, ir, pr)
