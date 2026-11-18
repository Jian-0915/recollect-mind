"""桌面端本地 TTS 服务管理（qwentts.cpp / tts-server）。

只做三件事：探测可执行文件与模型是否齐全、拉起并回收常驻服务、给
/api/tts 路由提供目标地址与服务状态。不写数据库、不联网、不碰主进程状态。

设计要点：
- 没文件就优雅降级（status.available=False），桌面端照常打字对话。
- 启动放后台线程，不阻塞窗口出现；模型加载约 2~3 秒，就绪后按钮才亮。
- 关窗时 terminate 子进程，不留残进程。
"""
from __future__ import annotations

import os
import subprocess
import sys
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
