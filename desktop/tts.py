"""桌面端本地 TTS 服务管理（qwentts.cpp / tts-server）。

只做三件事：探测可执行文件与模型是否齐全、拉起并回收常驻服务、给
/api/tts 路由提供目标地址与服务状态。不写数据库、不联网、不碰主进程状态。

设计要点：
- 没文件就优雅降级（status.available=False），桌面端照常打字对话。
- 启动放后台线程，不阻塞窗口出现；模型加载约 2~3 秒，就绪后按钮才亮。
- 关窗时 terminate 子进程，不留残进程。
"""
from __future__ import annotations

import hashlib
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TRIAL = ROOT / "_tts_trial" / "qwentts.cpp"

EXE = _TRIAL / "build-cpu" / "tts-server.exe"
MODEL = _TRIAL / "models" / "qwen-talker-1.7b-customvoice-Q8_0.gguf"
CODEC = _TRIAL / "models" / "qwen-tokenizer-12hz-Q8_0.gguf"

HOST = "127.0.0.1"
PORT = int(os.environ.get("NEUROMEM_TTS_PORT", "8179"))
BASE = f"http://{HOST}:{PORT}"

# 预合成缓存：每句回复产生时就后台合成好存盘，前端点播直读文件，秒开不卡。
CACHE_DIR = ROOT / "_tts_trial" / "tts_cache"
CACHE_MAX = int(os.environ.get("NEUROMEM_TTS_CACHE_MAX", "200"))  # 超出按 mtime 淘汰最旧的

# CustomVoice 内置音色；前端下拉展示用，服务端不强制白名单。
VOICES = ["vivian", "serena", "uncle_fu", "dylan", "eric"]
DEFAULT_VOICE = "vivian"

_proc = None
_state = {"phase": "off", "error": ""}   # off / starting / ready / failed


def available():
    """返回 (是否可用, 缺什么)。纯文件检查，不启动进程。"""
    missing = []
    for label, p in (("tts-server.exe", EXE), ("talker 模型", MODEL), ("codec 模型", CODEC)):
        if not p.is_file():
            missing.append(label)
    if missing:
        return False, "缺少 " + "、".join(missing)
    return True, ""


def is_ready():
    """服务是否已在监听并能列出模型。1 秒超时，用于每次合成前的快检。"""
    try:
        with urllib.request.urlopen(BASE + "/v1/models", timeout=1.0) as r:
            return r.status == 200
    except Exception:
        return False


def status():
    ok, why = available()
    ready = is_ready() if ok else False
    return {
        "ok": True,
        "available": ok,
        "ready": ready,
        "starting": _state["phase"] == "starting",
        "reason": "" if (ok and ready) else (why or _state["error"]),
        "voices": list(VOICES),
        "default_voice": DEFAULT_VOICE,
        "format": "pcm_s16le_24000_mono",
    }


def _wait_ready(timeout=40):
    """轮询就绪；服务进程提前退出就立刻失败，不空等。"""
    global _state
    end = time.time() + timeout
    while time.time() < end:
        if is_ready():
            _state.update(phase="ready", error="")
            return True
        if _proc is not None and _proc.poll() is not None:
            _state.update(phase="failed", error="服务进程退出 code=%s" % _proc.returncode)
            return False
        time.sleep(0.4)
    _state.update(phase="failed", error="就绪等待超时")
    return False


def start():
    """幂等启动。已有服务（外部起的）直接复用，不重复拉进程。"""
    global _proc, _state
    ok, why = available()
    if not ok:
        _state.update(phase="failed", error=why)
        return False
    if is_ready():
        _state.update(phase="ready", error="")
        return True
    if _proc is not None and _proc.poll() is None:
        return _wait_ready()

    _state.update(phase="starting", error="")
    flags = 0
    if sys.platform == "win32":
        # 不弹黑窗；独立进程组便于整组终止。
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | \
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    cmd = [
        str(EXE),
        "--model", str(MODEL),
        "--codec", str(CODEC),
        "--alias", "qwen3-tts-customvoice",
        "--host", HOST,
        "--port", str(PORT),
        "--lang", "Chinese",
    ]
    try:
        _proc = subprocess.Popen(
            cmd, cwd=str(_TRIAL), creationflags=flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        _state.update(phase="failed", error="%s: %s" % (type(e).__name__, str(e)[:160]))
        return False
    return _wait_ready()


def stop():
    """关窗时回收进程。失败静默，不能因为杀服务影响退出。"""
    global _proc, _state
    p, _proc = _proc, None
    if p is not None and p.poll() is None:
        try:
            p.terminate()
            try:
                p.wait(timeout=6)
            except subprocess.TimeoutExpired:
                p.kill()
        except Exception:
            pass
    _state.update(phase="off", error="")


# ---------------------------------------------------------------------------
# 预合成缓存
# 回复一产生就把文本交给后台队列合成成 wav 落盘；前端点播时直读缓存文件，
# 用浏览器原生 <audio> 播放——零等待、零卡顿。合成失败不影响文字对话。
# ---------------------------------------------------------------------------

_preq: "queue.Queue[tuple[str, str]]" = queue.Queue()
_pending: set[str] = set()          # 已入队/合成中的 key，避免同一句重复合成
_preworker: threading.Thread | None = None
_prelock = threading.Lock()


def cache_key(text: str, voice: str) -> str:
    """音色+文本决定唯一 key。同一句换音色是不同缓存，互不覆盖。"""
    raw = (voice or DEFAULT_VOICE) + "\x00" + (text or "").strip()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _cache_file(key: str) -> Path:
    return CACHE_DIR / (key + ".wav")


def has_cache(key: str) -> bool:
    """校验 key 格式 + 文件存在，防路径穿越。返回文件是否可读。"""
    if not key or len(key) != 20 or any(c not in "0123456789abcdef" for c in key):
        return False
    return _cache_file(key).is_file()


def cache_bytes(key: str) -> bytes | None:
    if not has_cache(key):
        return None
    try:
        return _cache_file(key).read_bytes()
    except OSError:
        return None


def cache_count() -> int:
    try:
        return len(list(CACHE_DIR.glob("*.wav")))
    except OSError:
        return 0


def _evict() -> None:
    """超出上限按 mtime 删最旧，控制磁盘占用。"""
    try:
        files = sorted(CACHE_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        over = len(files) - CACHE_MAX
        for p in files[:max(0, over)]:
            try:
                p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _synthesize(text: str, voice: str, key: str) -> bool:
    """调 tts-server 合成整句 wav 并原子落盘。返回是否成功。"""
    import requests
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    body = {
        "input": text,
        "voice": voice if voice in VOICES else DEFAULT_VOICE,
        "language": "Chinese",
        "response_format": "wav",   # 带头 wav，浏览器 <audio> 可直接播
        "seed": 42,
    }
    try:
        r = requests.post(BASE + "/v1/audio/speech", json=body, timeout=(5, 120))
        if r.status_code != 200 or not r.content.startswith(b"RIFF"):
            return False
        tmp = _cache_file(key).with_suffix(".wav.tmp")
        tmp.write_bytes(r.content)
        tmp.replace(_cache_file(key))   # 原子替换，避免读到半截文件
        _evict()
        return True
    except Exception:
        return False


def _worker() -> None:
    """单线程串行合成，避免并发抢 CPU 把桌面端拖慢。"""
    while True:
        text, voice, key = _preq.get()
        try:
            # start() 幂等：服务已在跑就直接返回，否则后台拉起（约 2~4 秒）。
            # 进程退出时 daemon 线程随之消失，无需显式停 worker。
            if start():
                _synthesize(text, voice, key)
        finally:
            with _prelock:
                _pending.discard(key)
            _preq.task_done()


def _ensure_worker() -> None:
    global _preworker
    with _prelock:
        if _preworker is None or not _preworker.is_alive():
            _preworker = threading.Thread(target=_worker, name="tts-precache", daemon=True)
            _preworker.start()


def precache(text: str, voice: str | None = None) -> str:
    """入队预合成，立即返回 key（不阻塞调用方）。

    服务不可用/文本空时返回空串——上层据此可以不显示按钮，但默认已显示，
    失败只是这次没缓存，点播时走兜底实时合成。
    """
    text = (text or "").strip()
    if not text or len(text) > 400:
        return ""
    ok, _ = available()
    if not ok:
        return ""
    voice = (voice or DEFAULT_VOICE).strip()
    if voice not in VOICES:
        voice = DEFAULT_VOICE
    key = cache_key(text, voice)
    if has_cache(key):
        return key
    with _prelock:
        if key in _pending:
            return key
        _pending.add(key)
    _ensure_worker()
    _preq.put((text, voice, key))
    return key


