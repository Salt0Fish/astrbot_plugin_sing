"""音源层：QQ 音乐优先，失败自动降级到网易云。

QQ 音乐的 VIP cookie 放在本文件同目录的 qqmusic.json：
    {"uin": "你的QQ号", "qm_keyst": "从 y.qq.com 复制的值"}
没有这个文件时只走网易云。
"""

import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
CFG_PATH = HERE / "qqmusic.json"
FF = __import__("os").environ.get("FFMPEG_PATH", "ffmpeg")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

CFG = {"uin": "", "qm_keyst": ""}
if CFG_PATH.exists():
    try:
        CFG.update(json.loads(CFG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
QQ_COOKIE = f"uin={CFG.get('uin','')}; qm_keyst={CFG.get('qm_keyst','')}"
HAS_QQ = bool(CFG.get("uin") and CFG.get("qm_keyst"))


def norm(s: str) -> str:
    return re.sub(r"[\s\-_·、,，.。()（）\[\]【】'\"!！?？~]", "", (s or "").lower())


def dur_of(path: Path) -> float:
    p = subprocess.run([FF, "-i", str(path)], capture_output=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)",
                  (p.stderr or b"").decode("utf-8", "replace"))
    if not m:
        return 0.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def download(url: str, out: Path) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://y.qq.com/"})
    total = 0
    with urllib.request.urlopen(req, timeout=90) as r, out.open("wb") as f:
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
    return total


def qq_search(kw: str, n: int = 10) -> list[dict]:
    url = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp?" + urllib.parse.urlencode(
        {"w": kw, "format": "json", "n": n, "p": 1, "cr": 1, "new_json": 1}
    )
    d = json.loads(urllib.request.urlopen(urllib.request.Request(
        url, headers={"User-Agent": UA, "Referer": "https://y.qq.com/"}), timeout=20).read())
    out = []
    for s in d["data"]["song"]["list"]:
        out.append({
            "source": "qq",
            "id": s.get("mid") or s.get("songmid"),
            "name": s.get("name") or s.get("title"),
            "singer": "、".join(x.get("name", "") for x in (s.get("singer") or [])),
            "interval": float(s.get("interval") or 0),
        })
    return out


def ne_search(kw: str, n: int = 10) -> list[dict]:
    url = "https://music.163.com/api/search/get/web?" + urllib.parse.urlencode(
        {"s": kw, "type": 1, "limit": n})
    d = json.loads(urllib.request.urlopen(urllib.request.Request(
        url, headers={"User-Agent": UA, "Referer": "https://music.163.com/"}), timeout=20).read())
    songs = (d.get("result") or {}).get("songs") or []
    return [{
        "source": "netease",
        "id": s["id"],
        "name": s["name"],
        "singer": "、".join(a.get("name", "") for a in (s.get("artists") or [])),
        "interval": (s.get("duration") or 0) / 1000,
    } for s in songs]


def score(query: str, item: dict) -> float:
    q, n, sg = norm(query), norm(item.get("name", "")), norm(item.get("singer", ""))
    if not n:
        return 0
    sc = 0.0
    if n in q:
        sc += len(n) * 2
    elif q in n:
        sc += len(q)
    rest = q.replace(n, "") if n in q else q
    for tok in re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fa5]{2,}", rest):
        if tok in sg:
            sc += 5
    for tok in re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fa5]{2,}", sg):
        if tok in q:
            sc += 5
    if n == q:
        sc += 6
    return sc


def qq_url(item: dict) -> str | None:
    if not HAS_QQ:
        return None
    body = {
        "req_1": {
            "module": "music.vkey.GetVkey",
            "method": "UrlGetVkey",
            "param": {"guid": "10000", "songmid": [item["id"]], "songtype": [0],
                      "uin": CFG["uin"], "loginflag": 1, "platform": "20"},
        },
        "comm": {"uin": CFG["uin"], "format": "json", "ct": 24, "cv": 0},
    }
    req = urllib.request.Request(
        "https://u.y.qq.com/cgi-bin/musicu.fcg",
        data=json.dumps(body).encode(),
        headers={"User-Agent": UA, "Referer": "https://y.qq.com/",
                 "Cookie": QQ_COOKIE, "Content-Type": "application/json"},
    )
    d = json.loads(urllib.request.urlopen(req, timeout=25).read())
    data = (d.get("req_1") or {}).get("data") or {}
    info = (data.get("midurlinfo") or [{}])[0]
    purl = info.get("purl") or ""
    if not purl:
        return None
    sip = (data.get("sip") or ["https://dl.stream.qqmusic.qq.com/"])[0]
    return sip + purl


def ne_url(item: dict) -> str | None:
    return f"https://music.163.com/song/media/outer/url?id={item['id']}.mp3"


def fetch(kw: str, work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    cands: list[dict] = []
    errors: list[str] = []
    searchers = [qq_search, ne_search] if HAS_QQ else [ne_search]
    for fn in searchers:
        try:
            for it in fn(kw):
                it["score"] = score(kw, it)
                if it["score"] > 0:
                    cands.append(it)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{fn.__name__}:{exc}")
    if not cands:
        return {"ok": False, "reason": "无匹配结果 " + " | ".join(errors)}

    cands.sort(key=lambda x: (-x["score"], 0 if x["source"] == "qq" else 1))
    for it in cands[:6]:
        try:
            url = qq_url(it) if it["source"] == "qq" else ne_url(it)
            if not url:
                errors.append(f"{it['source']}-{it['name']}:无地址")
                continue
            ext = "m4a" if it["source"] == "qq" else "mp3"
            out = work / f"src_{it['source']}.{ext}"
            size = download(url, out)
            if size < 300 * 1024:
                errors.append(f"{it['source']}-{it['name']}:文件过小")
                continue
            d = dur_of(out)
            if it["interval"] and d < it["interval"] * 0.6:
                errors.append(f"{it['source']}-{it['name']}:疑似试听")
                continue
            if d < 30:
                errors.append(f"{it['source']}-{it['name']}:时长过短")
                continue
            return {"ok": True, "title": it["name"], "artist": it["singer"],
                    "path": out, "dur": d, "source": it["source"]}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{it['source']}-{it['name']}:{exc}")
    return {"ok": False, "reason": " | ".join(errors[-4:])}
