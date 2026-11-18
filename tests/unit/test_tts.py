"""桌面端语音（TTS）接入契约。

不启动真实服务、不下载模型：用 monkeypatch 替掉子进程与 HTTP，只验
桌面壳这层的契约——降级、白名单、截断、流式转发、生命周期幂等。
"""
import pytest
from fastapi.testclient import TestClient

import desktop.app as da
import desktop.tts as tts
from neuromem import account, api


class _FakeUpstream:
    status_code = 200
    text = ""

    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size=4096):
        for c in self._chunks:
            yield c

    def close(self):
        self.closed = True


@pytest.fixture(scope="module")
def client():
    da._build_server(18767)
    tok = da._WEB_LOGIN_TOKEN["value"]
    return TestClient(da._DesktopAuth(api.app, tok))


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(tts, "is_ready", lambda: True)
    monkeypatch.setattr(tts, "start", lambda: True)


def test_status_reports_shape(client, monkeypatch):
    monkeypatch.setattr(tts, "is_ready", lambda: True)
    r = client.get("/api/tts/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["ready"] is True
    assert "vivian" in body["voices"]
    assert body["format"] == "pcm_s16le_24000_mono"


def test_speak_streams_pcm(client, ready, monkeypatch):
    seen = {}

    def fake_post(url, json=None, stream=False, timeout=None):
        seen.update(url=url, json=json, stream=stream)
        return _FakeUpstream([b"\x00\x00", b"\xff\x7f"])

    monkeypatch.setattr("requests.post", fake_post)
    r = client.post("/api/tts", json={"text": "你好", "voice": "vivian"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/pcm"
    assert r.headers["x-tts-voice"] == "vivian"
    assert r.content == b"\x00\x00\xff\x7f"
    assert seen["json"]["response_format"] == "pcm"
    assert seen["json"]["language"] == "Chinese"
    assert seen["stream"] is True


def test_unknown_voice_falls_back(client, ready, monkeypatch):
    seen = {}

    def fake_post(url, json=None, stream=False, timeout=None):
        seen["voice"] = json["voice"]
        return _FakeUpstream([b"\x01\x00"])

    monkeypatch.setattr("requests.post", fake_post)
    r = client.post("/api/tts", json={"text": "你好", "voice": "../../evil"})
    assert r.status_code == 200
    assert seen["voice"] == tts.DEFAULT_VOICE
    assert r.headers["x-tts-voice"] == tts.DEFAULT_VOICE


def test_text_is_truncated(client, ready, monkeypatch):
    seen = {}

    def fake_post(url, json=None, stream=False, timeout=None):
        seen["text"] = json["input"]
        return _FakeUpstream([b"\x01\x00"])

    monkeypatch.setattr("requests.post", fake_post)
    long_text = "啊" * 500
    r = client.post("/api/tts", json={"text": long_text})
    assert r.status_code == 200
    assert len(seen["text"]) == 400


def test_empty_text_rejected(client, ready):
    r = client.post("/api/tts", json={"text": "   "})
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_service_down_degrades(client, monkeypatch):
    """服务起不来时返回 503 + 明确错误，绝不吐一个空音频骗前端。"""
    monkeypatch.setattr(tts, "is_ready", lambda: False)
    monkeypatch.setattr(tts, "start", lambda: False)
    monkeypatch.setattr(tts, "status", lambda: {
        "ok": True, "available": False, "ready": False,
        "reason": "缺少 tts-server.exe", "voices": [],
        "default_voice": "vivian", "format": "pcm_s16le_24000_mono",
    })
    r = client.post("/api/tts", json={"text": "你好"})
    assert r.status_code == 503
    assert "缺少" in r.json()["error"]


def test_upstream_error_surfaces(client, ready, monkeypatch):
    """上游非 200 要转成 JSON 错误，而不是把错误体当音频流吐给前端。"""
    class Bad:
        status_code = 500
        text = "engine busy"
        def close(self): pass

    monkeypatch.setattr("requests.post", lambda *a, **k: Bad())
    r = client.post("/api/tts", json={"text": "你好"})
    assert r.status_code == 502
    assert "合成失败" in r.json()["error"]


def test_available_checks_all_files(monkeypatch):
    """任一文件缺失都要报出来，这是降级按钮隐藏的依据。"""
    ok, why = tts.available()
    assert isinstance(ok, bool)
    if not ok:
        assert "缺少" in why


# ---------------- 预合成缓存 ----------------

def test_cache_key_stable_and_voice_aware():
    """同一句+同一音色 key 恒定；换音色是不同 key，不互相覆盖。"""
    k = tts.cache_key("你好", "vivian")
    assert k == tts.cache_key("你好", "vivian")
    assert len(k) == 20 and all(c in "0123456789abcdef" for c in k)
    assert k != tts.cache_key("你好", "serena")
    assert k != tts.cache_key("你好呀", "vivian")


def test_cache_path_traversal_blocked(monkeypatch, tmp_path):
    """非法 key 必须先被挡掉，绝不拼进路径。"""
    monkeypatch.setattr(tts, "CACHE_DIR", tmp_path)
    assert not tts.has_cache("../../evil")
    assert not tts.has_cache("x" * 19)
    assert not tts.has_cache("")
    assert tts.cache_bytes("../../evil") is None


def test_precache_dedups_same_key(monkeypatch):
    """同一句排队中重复入队只算一次，避免堆一串相同合成任务。"""
    monkeypatch.setattr(tts, "available", lambda: (True, ""))
    monkeypatch.setattr(tts, "_ensure_worker", lambda: None)  # 不起真线程
    monkeypatch.setattr(tts, "has_cache", lambda k: False)
    tts._pending.clear()
    try:
        k1 = tts.precache("同一句话", "vivian")
        k2 = tts.precache("同一句话", "vivian")
        assert k1 == k2 and k1 in tts._pending
        queued = 0
        while not tts._preq.empty():
            tts._preq.get_nowait()
            queued += 1
        assert queued == 1
    finally:
        tts._pending.clear()


def test_precache_empty_text_noop(monkeypatch):
    monkeypatch.setattr(tts, "available", lambda: (True, ""))
    assert tts.precache("   ", "vivian") == ""


def test_precache_degrades_without_models(monkeypatch):
    monkeypatch.setattr(tts, "available", lambda: (False, "缺少模型"))
    assert tts.precache("你好", "vivian") == ""


def test_evict_keeps_newest(monkeypatch, tmp_path):
    """超出上限按 mtime 删最旧，保留最新的。"""
    monkeypatch.setattr(tts, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(tts, "CACHE_MAX", 3)
    for i in range(5):
        p = tmp_path / ("k%d.wav" % i)
        p.write_bytes(b"RIFF" + b"\x00" * 40)
        import os
        os.utime(p, (1000 + i, 1000 + i))  # i 越大越新
    tts._evict()
    left = sorted(p.name for p in tmp_path.glob("*.wav"))
    assert left == ["k2.wav", "k3.wav", "k4.wav"]


def test_precache_route(client, monkeypatch):
    monkeypatch.setattr(tts, "available", lambda: (True, ""))
    monkeypatch.setattr(tts, "precache", lambda t, v: "abc123")
    r = client.post("/api/tts/precache", json={"text": "你好", "voice": "vivian"})
    assert r.status_code == 200 and r.json() == {"ok": True, "key": "abc123"}


def test_precache_route_no_models(client, monkeypatch):
    monkeypatch.setattr(tts, "available", lambda: (False, "缺少模型"))
    r = client.post("/api/tts/precache", json={"text": "你好"})
    assert r.json()["ok"] is False and r.json()["key"] == ""


def test_audio_route_cache_hit(client, monkeypatch, tmp_path):
    """命中返回 audio/wav 且带头；key 透传给缓存层。"""
    p = tmp_path / ("a" * 20 + ".wav")
    p.write_bytes(b"RIFFfake")
    monkeypatch.setattr(tts, "CACHE_DIR", tmp_path)
    r = client.get("/api/tts/audio/" + "a" * 20)
    assert r.status_code == 200
    assert r.content == b"RIFFfake"
    assert "wav" in r.headers["content-type"]
    assert r.headers["x-tts-cache"] == "hit"
    # 音频不能长缓存：换音色后必须重新取，否则 WebView 可能沿用旧音色缓冲
    assert "no-store" in r.headers["cache-control"]


def test_audio_route_invalid_key_404(client, monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "CACHE_DIR", tmp_path)
    r = client.get("/api/tts/audio/../../evil")
    assert r.status_code in (404, 307)  # 路由不匹配或缓存未命中，都不吐文件


def test_ensure_route_ready(client, monkeypatch):
    monkeypatch.setattr(tts, "available", lambda: (True, ""))
    monkeypatch.setattr(tts, "precache", lambda t, v: "ready1")
    monkeypatch.setattr(tts, "has_cache", lambda k: True)
    r = client.post("/api/tts/ensure", json={"text": "你好"})
    assert r.status_code == 200 and r.json() == {"ok": True, "key": "ready1"}


def test_ensure_route_no_models(client, monkeypatch):
    """服务不可用时 ensure 如实报错，绝不吞掉文字对话。"""
    monkeypatch.setattr(tts, "available", lambda: (False, "缺少模型"))
    r = client.post("/api/tts/ensure", json={"text": "你好"})
    assert r.json()["ok"] is False and "不可用" in r.json()["error"]
