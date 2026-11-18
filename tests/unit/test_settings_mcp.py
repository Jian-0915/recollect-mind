"""settings 层多 MCP 服务配置：加密落库、掩码展示、启用切换、缓存失效。"""
import pytest

from neuromem import secret, settings


def test_mcp_default_when_empty(conn):
    got = settings.mcp_load()
    assert len(got) == 1
    assert got[0]["id"] == "anysearch"
    assert got[0]["enabled"] is True
    assert got[0]["tool"] == "search"


def test_mcp_save_encrypts_key(conn):
    settings.mcp_save([
        {"name": "我的服务", "url": "https://mcp.example.com/mcp",
         "api_key": "sk-secret-123456", "tool": "web_search", "enabled": True},
    ])
    # 直接从库读原始 JSON：密钥必须是 DPAPI 密文，不能是明文
    row = conn.execute("SELECT value FROM settings WHERE key='mcp_servers'").fetchone()
    assert row is not None
    assert "sk-secret-123456" not in row["value"]
    assert secret.PREFIX in row["value"]

    loaded = settings.mcp_load()
    assert loaded[0]["api_key"] == "sk-secret-123456"
    assert loaded[0]["tool"] == "web_search"


def test_mcp_public_masks_key(conn):
    settings.mcp_save([
        {"name": "A", "url": "https://a.com/mcp", "api_key": "abcdefgh1234"},
    ])
    pub = settings.mcp_public()
    assert pub[0]["api_key"].startswith("****")
    assert "abcdefgh1234" not in pub[0]["api_key"]
    assert pub[0]["api_key_info"]["set"] is True
    assert pub[0]["api_key_info"]["tail"].endswith("1234")


def test_mcp_normalize_filters_invalid(conn):
    settings.mcp_save([
        {"name": "bad", "url": "not-a-url"},             # 非 http(s) 丢弃
        {"name": "ok", "url": "https://ok.com/mcp", "id": "x", "bogus": 1},
        {"name": "dup", "url": "https://dup.com/mcp", "id": "x"},  # 重复 id 丢弃
    ])
    got = settings.mcp_load()
    assert len(got) == 1
    assert got[0]["id"] == "x"
    assert "bogus" not in got[0]


def test_mcp_active_prefers_enabled(conn):
    settings.mcp_save([
        {"name": "off", "url": "https://off.com/mcp", "api_key": "1", "enabled": False},
        {"name": "on", "url": "https://on.com/mcp", "api_key": "2", "enabled": True},
    ])
    assert settings.mcp_active()["name"] == "on"


def test_mcp_active_falls_back_when_none_enabled(conn):
    settings.mcp_save([
        {"name": "a", "url": "https://a.com/mcp", "enabled": False},
        {"name": "b", "url": "https://b.com/mcp", "enabled": False},
    ])
    assert settings.mcp_active()["name"] == "a"


def test_mcp_legacy_plaintext_readable(conn):
    """旧库里的明文密钥仍能读取（兼容迁移前数据）。"""
    import json
    settings.mcp_load()  # 触发建表
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('mcp_servers',?)",
                 (json.dumps([{"id": "old", "name": "old", "url": "https://old.com/mcp",
                               "api_key": "plainkey", "tool": "search", "enabled": True}]),))
    conn.commit()
    assert settings.mcp_load()[0]["api_key"] == "plainkey"


def test_mcp_clear_via_empty_list(conn):
    settings.mcp_save([{"name": "A", "url": "https://a.com/mcp", "api_key": "k"}])
    settings.mcp_save([])
    assert settings.mcp_load() == []
    assert settings.mcp_active() is None
