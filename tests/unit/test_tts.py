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
