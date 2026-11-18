"""语音播放偏好的后端持久化。

回归背景：偏好曾只存浏览器 localStorage，桌面端每次启动端口顺延，
localStorage 按 origin（含端口）隔离——端口一变就"丢失"，用户实测每次
重启都要重新设。现在以后端 settings 表为准（跟着主库、不依赖端口）。

这些用例验证：默认值、存取往返、非法值夹取、落库位置、跨"重启"（重新
load）仍在、以及 /api/tts/prefs 路由。不碰主库、不连语音服务。
"""
import pytest
from fastapi.testclient import TestClient

from neuromem import api, settings


@pytest.fixture
def client(monkeypatch, memdb):
    # 鉴权放行（不碰真实 token 文件）；memdb 让路由读到临时库
    monkeypatch.setattr(api.auth, "allowed", lambda request: True)
    return TestClient(api.app)


def test_default_when_empty(conn):
    got = settings.tts_prefs_load()
    assert got == {"voice": "vivian", "rate": 1.0, "volume": 1.0}


def test_save_and_load_roundtrip(conn):
    saved = settings.tts_prefs_save({"voice": "eric", "rate": 1.15, "volume": 0.55})
    assert saved["voice"] == "eric"
    # 重新读（模拟下次启动、端口已变）仍拿得到
    assert settings.tts_prefs_load() == {"voice": "eric", "rate": 1.15, "volume": 0.55}


def test_normalize_clamps_and_falls_back(conn):
    got = settings.tts_prefs_save({
        "voice": "not-a-voice",   # 非法音色 → 默认
        "rate": 99,               # 越界 → 夹到上限
        "volume": -3,             # 越界 → 夹到下限
    })
    assert got["voice"] == "vivian"
    assert got["rate"] == 1.4
    assert got["volume"] == 0.0


def test_partial_save_keeps_defaults_for_missing(conn):
    got = settings.tts_prefs_save({"voice": "serena"})
    assert got["voice"] == "serena"
    assert got["rate"] == 1.0
    assert got["volume"] == 1.0


def test_persisted_in_settings_table(conn):
    settings.tts_prefs_save({"voice": "dylan", "rate": 0.9, "volume": 0.7})
    row = conn.execute("SELECT value FROM settings WHERE key='tts_prefs'").fetchone()
    assert row is not None
    assert "dylan" in row["value"]


def test_api_get_put(client):
    """路由读写：GET 返回默认，PUT 后 GET 拿到新值。"""
    r = client.get("/api/tts/prefs")
    assert r.status_code == 200
    assert r.json()["prefs"]["voice"] == "vivian"

    r = client.put("/api/tts/prefs", json={"voice": "uncle_fu", "rate": 1.2, "volume": 0.6})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = client.get("/api/tts/prefs")
    assert r.json()["prefs"] == {"voice": "uncle_fu", "rate": 1.2, "volume": 0.6}


def test_frontend_persistence_wiring():
    """前端必须：从后端拉取偏好回填、改动时 PUT 后端。防改回纯 localStorage。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "desktop" / "web"
    logic = (root / "logic.js").read_text(encoding="utf-8")
    app = (root / "assets" / "app.js").read_text(encoding="utf-8")
    # logic.js 暴露桥接
    assert "ttsPrefs" in logic and "ttsPrefsSave" in logic
    assert "/api/tts/prefs" in logic
    # app.js：启动时从后端同步 + 保存时回写后端
    assert "syncTtsPrefsFromServer" in app
    assert "DeskAPI.ttsPrefs()" in app
    assert "DeskAPI.ttsPrefsSave" in app
