"""MCP 服务管理路由测试：不联网、不碰主库。"""
import pytest
from fastapi.testclient import TestClient

from neuromem import api, settings, tools


@pytest.fixture()
def client(monkeypatch):
    # 测试环境存在真实 access_token.txt，鉴权中间件会拦；这里放行，
    # 不碰真实 token 文件（改文件属于污染主库数据）。
    monkeypatch.setattr(api.auth, "allowed", lambda request: True)
    return TestClient(api.app)


def test_get_servers_default(client):
    r = client.get("/api/mcp/servers")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and len(d["servers"]) == 1
    assert d["active_id"] == "anysearch"
    assert "api_key" in d["servers"][0]  # 掩码字段存在


def test_put_servers_masks_and_encrypts(client, conn):
    payload = {"servers": [
        {"id": "x", "name": "X", "url": "https://x.com/mcp",
         "api_key": "sk-abcdef1234", "tool": "search", "enabled": True},
    ]}
    r = client.put("/api/mcp/servers", json=payload)
    assert r.json()["ok"]
    # 对外掩码，不回显明文
    assert r.json()["servers"][0]["api_key"].startswith("****")
    # 库里是密文
    row = conn.execute("SELECT value FROM settings WHERE key='mcp_servers'").fetchone()
    assert "sk-abcdef1234" not in row["value"]
    # 内部读到明文
    assert settings.mcp_load()[0]["api_key"] == "sk-abcdef1234"


def test_put_servers_keeps_old_key_on_mask(client, conn):
    client.put("/api/mcp/servers", json={"servers": [
        {"id": "x", "name": "X", "url": "https://x.com/mcp",
         "api_key": "sk-keep-1234", "tool": "search", "enabled": True}]})
    # 前端回传掩码
    client.put("/api/mcp/servers", json={"servers": [
        {"id": "x", "name": "X2", "url": "https://x2.com/mcp",
         "api_key": "****1234", "tool": "search", "enabled": True}]})
    assert settings.mcp_load()[0]["api_key"] == "sk-keep-1234"
    assert settings.mcp_load()[0]["name"] == "X2"


def test_put_servers_rejects_non_array(client):
    r = client.put("/api/mcp/servers", json={"servers": "nope"})
    assert not r.json()["ok"]


def test_set_active(client, conn):
    client.put("/api/mcp/servers", json={"servers": [
        {"id": "a", "name": "A", "url": "https://a.com/mcp", "enabled": True},
        {"id": "b", "name": "B", "url": "https://b.com/mcp", "enabled": False}]})
    r = client.post("/api/mcp/active", json={"id": "b"})
    assert r.json()["ok"] and r.json()["active_id"] == "b"
    assert settings.mcp_active()["id"] == "b"


def test_set_active_unknown(client):
    r = client.post("/api/mcp/active", json={"id": "ghost"})
    assert not r.json()["ok"]


def test_test_server_ok(client, monkeypatch):
    monkeypatch.setattr(tools.AnySearchProvider, "test_connection",
                        lambda self: {"ok": True, "tools": ["search"], "has_search": True})
    r = client.post("/api/mcp/test", json={"url": "https://x.com/mcp",
                                           "api_key": "k", "tool": "search"})
    assert r.json()["ok"] and r.json()["has_search"]


def test_test_server_failure(client, monkeypatch):
    def boom(self):
        raise tools.SearchError("connect refused")
    monkeypatch.setattr(tools.AnySearchProvider, "test_connection", boom)
    r = client.post("/api/mcp/test", json={"url": "https://bad.com/mcp"})
    assert not r.json()["ok"]
    assert "connect refused" in r.json()["error"]
