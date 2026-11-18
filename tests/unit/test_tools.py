"""工具层测试：全程假数据，不发任何真实网络请求。"""
import pytest

from neuromem import tools


SAMPLE = """## Search Results (10 results, 1345ms)

### 1. 乌江寨官网
- **URL**: https://www.wujiangvillage.com/
- 贵州乌江寨国际旅游度假区官网，含门票与出行锦囊。

### 2. 乌江寨攻略
- **URL**: https://example.com/guide
- 吊脚楼建筑群、非遗打铁花。
- 2026 年开放时间更新。

```yaml
request_id: abc123
```
"""


def test_parse_search_text():
    got = tools.parse_search_text(SAMPLE)
    assert len(got) == 2
    assert got[0].title == "乌江寨官网"
    assert got[0].url == "https://www.wujiangvillage.com/"
    assert "出行锦囊" in got[0].snippet
    assert "吊脚楼" in got[1].snippet and "2026" in got[1].snippet
    assert all(r.request_id == "abc123" for r in got)
    assert all(r.fetched_at for r in got)


def test_as_prompt_has_source():
    r = tools.parse_search_text(SAMPLE)[0]
    p = r.as_prompt(3)
    assert "[3]" in p and "https://" in p


def test_parse_empty_raises():
    with pytest.raises(tools.SearchError):
        tools.parse_search_text("")


def test_parse_garbage_raises():
    with pytest.raises(tools.SearchError):
        tools.parse_search_text("nothing useful here")


def test_sanitize_query_removes_private_data():
    raw = '帮我查“微微昨天说她叫阿剑” 手机 13812345678 邮箱 a@b.com 见 https://x.com/abc'
    got = tools.sanitize_query(raw, private_terms=["微微", "阿剑"])
    assert "13812345678" not in got
    assert "a@b.com" not in got
    assert "https://" not in got
    assert "微微" not in got and "阿剑" not in got
    assert "微微昨天说她叫阿剑" not in got
    assert got.strip()


def test_sanitize_query_empty():
    assert tools.sanitize_query('   ', []) == ''


def test_provider_search_uses_mock_rpc(monkeypatch):
    p = tools.AnySearchProvider()
    seen = {}

    def fake_rpc(method, params):
        seen["method"] = method
        seen["params"] = params
        return {"content": [{"type": "text", "text": SAMPLE}]}

    monkeypatch.setattr(p, "_rpc", fake_rpc)
    got = p.search("乌江寨", max_results=3)
    assert seen["method"] == "tools/call"
    assert seen["params"]["name"] == "search"
    assert seen["params"]["arguments"] == {"query": "乌江寨", "max_results": 3}
    assert len(got) == 2


def test_provider_search_rejects_empty():
    with pytest.raises(tools.SearchError):
        tools.AnySearchProvider().search("  ")


def test_provider_extract_parses_json(monkeypatch):
    p = tools.AnySearchProvider()
    monkeypatch.setattr(p, "_rpc", lambda m, pa: {
        "content": [{"type": "text", "text": '{"url":"https://a.com","title":"A","content":"正文"}'}]})
    assert p.extract("https://a.com") == "正文"


def test_provider_extract_rejects_non_url():
    with pytest.raises(tools.SearchError):
        tools.AnySearchProvider().extract("javascript:alert(1)")


def test_unknown_provider_raises():
    with pytest.raises(tools.SearchError):
        tools.get_provider("nonexistent")


def test_get_provider_uses_active_server(conn):
    """get_provider 从 settings 的启用服务构造端点/密钥/工具名。"""
    from neuromem import settings
    settings.mcp_save([{
        "name": "自建", "url": "https://my-mcp.example.com/mcp",
        "api_key": "sk-xyz", "tool": "web_search", "enabled": True,
    }])
    p = tools.get_provider()
    assert p.endpoint == "https://my-mcp.example.com/mcp"
    assert p.tool == "web_search"
    assert p._key_override == "sk-xyz"


def test_get_provider_no_server_raises(conn):
    from neuromem import settings
    settings.mcp_save([])
    with pytest.raises(tools.SearchError):
        tools.get_provider()


def test_get_provider_by_id(conn):
    from neuromem import settings
    settings.mcp_save([
        {"id": "a", "name": "A", "url": "https://a.com/mcp", "tool": "search", "enabled": True},
        {"id": "b", "name": "B", "url": "https://b.com/mcp", "tool": "s", "enabled": False},
    ])
    assert tools.get_provider("b").endpoint == "https://b.com/mcp"


def test_provider_uses_custom_tool_name(monkeypatch):
    p = tools.AnySearchProvider(tool="web_search")
    seen = {}

    def fake_rpc(method, params):
        seen.update(params)
        return {"content": [{"type": "text", "text": SAMPLE}]}

    monkeypatch.setattr(p, "_rpc", fake_rpc)
    p.search("乌江寨")
    assert seen["name"] == "web_search"


def test_test_connection(monkeypatch):
    p = tools.AnySearchProvider(tool="search")
    monkeypatch.setattr(p, "_rpc", lambda m, pa: {
        "tools": [{"name": "search"}, {"name": "extract"}]})
    got = p.test_connection()
    assert got["ok"] is True and got["has_search"] is True
    assert "search" in got["tools"]


def test_rpc_body_stream_compat():
    sse = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":1}}\n\n'
    assert tools._parse_rpc_body(sse, "text/event-stream")["result"]["ok"] == 1
    assert tools._parse_rpc_body('{"jsonrpc":"2.0","id":1,"result":{"ok":2}}',
                                 "application/json")["result"]["ok"] == 2
