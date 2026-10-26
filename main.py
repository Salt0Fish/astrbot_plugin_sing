"""唱歌：说"想听你唱歌"，用她自己的音色翻唱整首 + 发一段副歌语音。

流程：
  触发词 → 问歌名（或"唱首X"直接点歌）→ 后台跑 backend/cover.py
  → 先发整首 mp3 文件，再发 45 秒副歌语音条

backed 需要单独的环境（RVC + demucs + fairseq），配置里指过去：
  rvc_python   那个环境的 python.exe
  backend_dir  backend 目录（默认本插件下的 backend/）
  ffmpeg_path  ffmpeg 可执行文件
"""

import asyncio
import hashlib
import random
import re
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File, Plain, Record

PLUGIN_DIR = Path(__file__).parent
DATA_DIR = PLUGIN_DIR / "data"

TRIGGER = re.compile(r"(想听|要听|给我)?你?(唱|唱歌|唱首|唱一个|来一首)")
CANCEL = re.compile(r"^(算了|不用了|不听了|取消)$")

PERSONA = (
    "你是月宝（也叫熙宝），说话极短：一条通常5个字，最多9个字，"
    "爱用「呀、嘛、啦、哈哈、555」，几乎不用句号，会撒娇也会损人，不用客服腔。"
)
ASK_POOL = ["想听什么呀", "唱什么好呢", "你说歌名嘛", "想听啥", "哪首呀"]
START_POOL = ["等我一下", "我去找找", "好，等我", "那我去弄", "稍等哈"]
DONE_POOL = ["唱得不好听别笑我", "就这水平哈哈", "将就听吧", "", ""]
FAIL_POOL = ["这首没找到诶", "平台没这首，换个嘛", "弄不到这歌，换一首"]

CFG: dict = {}


def _cache_dir() -> Path:
    raw = CFG.get("cache_dir") or ""
    d = Path(raw) if raw else DATA_DIR / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Main(star.Star):
    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        self.context = context
        CFG.clear()
        CFG.update(dict(config))
        self.waiting: dict[str, float] = {}
        self.busy: dict[str, bool] = {}
        _cache_dir()

    async def _line(self, umo: str, task: str, pool: list[str]) -> str:
        fallback = random.choice(pool) if pool else ""
        try:
            prov = await self.context.get_using_provider_async(umo=umo)
            if prov is None:
                return fallback
            r = await prov.text_chat(
                prompt=f"{task}。只回一句话，不超过 10 个字，不要引号、不要解释。",
                system_prompt=PERSONA,
            )
            t = (getattr(r, "completion_text", "") or "").strip().strip("\"“”'")
            if 1 <= len(t) <= 14:
                return t
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[sing] 生成台词失败: {exc}")
        return fallback

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        umo = getattr(event, "unified_msg_origin", "") or ""
        if not umo or (CFG.get("scope", "FriendMessage") not in umo):
            return

        if umo in self.waiting and time.time() - self.waiting[umo] < 180:
            del self.waiting[umo]
            if CANCEL.match(text):
                yield event.plain_result(random.choice(["那下次嘛", "好哦，不唱了", "行吧哈哈"]))
                return
            event.stop_event()
            if self.busy.get(umo):
                yield event.plain_result(random.choice(["上一首还没弄完呢", "等我忙完这首"]))
                return
            self.busy[umo] = True
            asyncio.create_task(self._sing(umo, text))
            yield event.plain_result(await self._line(umo, f"对方点了《{text}》，你答应去找", START_POOL))
            return

        m = TRIGGER.search(text)
        if not m:
            return

        rest = TRIGGER.sub("", text)
        rest = re.sub(r"^[首个一]|(好不好|好吗|行吗|吧|呀|嘛|呢)+$", "", rest).strip(" ，,。")
        if len(rest) >= 2:
            event.stop_event()
            if self.busy.get(umo):
                yield event.plain_result(random.choice(["上一首还没弄完呢", "等我忙完这首"]))
                return
            self.busy[umo] = True
            asyncio.create_task(self._sing(umo, rest))
            yield event.plain_result(await self._line(umo, f"对方点了《{rest}》，你答应去找", START_POOL))
            return

        self.waiting[umo] = time.time()
        event.stop_event()
        yield event.plain_result(await self._line(umo, "对方想听你唱歌，你问他想听什么", ASK_POOL))

    async def _sing(self, umo: str, song: str) -> None:
        f0 = int(CFG.get("f0up_key", 0))
        ir = float(CFG.get("index_rate", 0.75))
        pr = float(CFG.get("protect", 0.33))
        try:
            key = hashlib.md5(f"{song}|{f0}|{ir}".encode()).hexdigest()[:16]
            cache = _cache_dir()
            full = cache / f"{key}_full.mp3"
            clip = cache / f"{key}_clip.wav"
            if full.exists() and clip.exists():
                await self._send(umo, full, clip, song, cached=True)
                return

            import os as _os

            backend = Path(CFG.get("backend_dir") or (PLUGIN_DIR / "backend"))
            rvc_python = CFG.get("rvc_python") or "python"
            cover = str(backend / "cover.py")
            env = dict(_os.environ)
            caches = PLUGIN_DIR / "data" / "cache_env"
            env.update({
                "TORCH_HOME": str(caches / "torch"),
                "HF_HOME": str(caches / "hf"),
                "HF_ENDPOINT": "https://hf-mirror.com",
                "TEMP": str(caches / "tmp"),
                "TMP": str(caches / "tmp"),
                "PYTHONIOENCODING": "utf-8",
                "FFMPEG_PATH": CFG.get("ffmpeg_path") or "ffmpeg",
                "PATH": (str(Path(CFG["ffmpeg_path"]).parent) + ";" + _os.environ.get("PATH", ""))
                if CFG.get("ffmpeg_path") else _os.environ.get("PATH", ""),
                "WORK_DIR": str(PLUGIN_DIR / "data" / "work"),
                "CLIP_SECONDS": str(int(CFG.get("clip_seconds", 45))),
                "RVC_MODEL": CFG.get("rvc_model") or "",
                "RVC_INDEX": CFG.get("rvc_index") or "",
            })
            for d in ("torch", "hf", "tmp", "work"):
                Path(env["TORCH_HOME"]).parent.joinpath(d).mkdir(parents=True, exist_ok=True)

            proc = await asyncio.create_subprocess_exec(
                rvc_python, "-u", cover, song, str(full), str(clip), str(f0), str(ir), str(pr),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
            )
            stdout, _ = await proc.communicate()
            log = (stdout or b"").decode("utf-8", "replace")
            logger.info(f"[sing] {song} 结束 rc={proc.returncode} {log[-200:]}")

            if full.exists() and clip.exists():
                await self._send(umo, full, clip, song)
            elif "NOT_FOUND" in log:
                await self._say(umo, "对方点的歌你没找到，跟他说一声", FAIL_POOL)
            else:
                await self._say(umo, "翻唱失败了，跟对方说一声",
                                ["唱失败了…", "弄砸了哈哈", "等我缓缓再试"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[sing] 异常: {exc}")
        finally:
            self.busy[umo] = False

    async def _say(self, umo: str, task: str, pool: list[str]) -> None:
        line = await self._line(umo, task, pool)
        if line:
            await self.context.send_message(umo, self._chain(line))

    async def _send(self, umo: str, full: Path, clip: Path, song: str, cached: bool = False) -> None:
        intro = await self._line(umo, f"你唱完了《{song}》，说一句把整首发给他",
                                 ["整首也给你", "整首发你啦", "完整版在这"])
        if intro:
            await self.context.send_message(umo, self._chain(intro))

        fchain = self._chain("")
        fchain.chain = [File(name=f"{song}_她唱的.mp3", file=str(full))]
        await self.context.send_message(umo, fchain)

        caption = ""
        if random.random() < 0.6:
            caption = await self._line(umo, f"再发一段《{song}》的高潮部分，说一句话", DONE_POOL)
        cchain = self._chain("")
        cchain.chain = [Record.fromFileSystem(str(clip))]
        if caption:
            cchain.chain.append(Plain(caption))
        await self.context.send_message(umo, cchain)
        logger.info(f"[sing] 已发送 {song} cached={cached} caption={caption!r}")

    def _chain(self, text: str):
        from astrbot.api.event import MessageChain

        return MessageChain([Plain(text)])
