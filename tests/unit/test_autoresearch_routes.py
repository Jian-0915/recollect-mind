"""F5 自主获取路由测试：状态/审计/开关，不联网、不碰主库。"""
import pytest
from fastapi.testclient import TestClient

from neuromem import api, autoresearch, knowledge, config


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(api.auth, "allowed", lambda request: True)
    return TestClient(api.app)


def test_status_route(client, monkeypatch):
    monkeypatch.setattr(autoresearch, "status", lambda: {
        "enabled": True, "daily_cap": 3, "gap_min": 30,
        "today_count": 1, "last_at": "2026-11-18T10:00:00"})
    r = client.get("/api/autoresearch/status")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["today_count"] == 1 and d["enabled"] is True


def test_log_route(client, monkeypatch):
    class FakeConn:
        def close(self): pass
    fake_conn = FakeConn()
    monkeypatch.setattr(knowledge, "connect", lambda: fake_conn)
    monkeypatch.setattr(knowledge, "list_autoresearch",
                        lambda c, limit: [{"entity": "乌江寨", "hit_count": 5}])
    r = client.get("/api/autoresearch/log?limit=10")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["items"][0]["entity"] == "乌江寨"


def test_toggle_route(client, monkeypatch):
    calls = []
    monkeypatch.setattr(autoresearch, "set_enabled", lambda on: calls.append(on))
    monkeypatch.setattr(autoresearch, "enabled", lambda: calls[-1] if calls else True)
    r = client.post("/api/autoresearch/toggle", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["ok"] and calls == [False]
