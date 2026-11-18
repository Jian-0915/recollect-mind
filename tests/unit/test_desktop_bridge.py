"""桌面端 → 网页后台的桥接契约。

桌面窗口里点「查看记忆」要走：openWeb(tab) → /desk-web-login?t=&tab=
→ 静默登录 → 落到后台指定标签页。这三步任何一步断了，用户就看不到记忆页。

不打真实浏览器、不碰主库：只验路由跳转、tab 白名单、会话注入。
"""
import pytest
from fastapi.testclient import TestClient

import desktop.app as da
from neuromem import account, api


@pytest.fixture(scope="module")
def client():
    # 注册 /desk 挂载、/desk-web-login、/api/desktop/meta 等桌面专属路由
    da._build_server(18765)
    tok = da._WEB_LOGIN_TOKEN["value"]
    # 用真实中间件包装：浏览器请求会被自动注入 cookie，等价静默登录
    return TestClient(da._DesktopAuth(api.app, tok)), tok


def _get(client, params):
    return client.get("/desk-web-login", params=params, follow_redirects=False)


def test_deep_link_to_memory_page(client):
    """带合法 tab 时跳到后台对应页（记忆页 = events）。"""
    c, tok = client
    r = _get(c, {"t": tok, "tab": "events"})
    assert r.status_code == 307
    assert r.headers["location"] == "/?tab=events"
    assert account.COOKIE in r.headers.get("set-cookie", "")


def test_deep_link_to_knowledge_page(client):
    """外部知识页也在白名单内——桌面端能直达「她知道了什么」的后台页。"""
    c, tok = client
    r = _get(c, {"t": tok, "tab": "knowledge"})
    assert r.status_code == 307
    assert r.headers["location"] == "/?tab=knowledge"
    assert account.COOKIE in r.headers.get("set-cookie", "")


def test_unknown_tab_falls_back_to_overview(client):
    """tab 不在白名单就回概览，绝不把外部输入拼进跳转地址。"""
    c, tok = client
    r = _get(c, {"t": tok, "tab": "javascript:alert(1)"})
    assert r.headers["location"] == "/"


def test_bad_token_never_logs_in(client):
    """token 不符：跳转可以发生，但绝不能种会话 cookie。"""
    c, _ = client
    r = _get(c, {"t": "not-a-token", "tab": "events"})
    assert r.headers["location"] == "/"
    assert account.COOKIE not in r.headers.get("set-cookie", "")


def test_backend_serves_tab_param(client):
    """后台首页带着 ?tab= 也能正常出页面，前端脚本负责切到记忆页。"""
    c, tok = client
    r = c.get("/", params={"tab": "events"})
    assert r.status_code == 200
    assert "switchTab" in r.text       # 深链切换脚本在
    assert "PAGE_META" in r.text
    assert 'data-tab="knowledge"' in r.text   # 外部知识 tab 在后台可见


def test_backend_exposes_knowledge_api(client):
    """后台能读到独立知识库：/api/knowledge 返回 ok 与 items。"""
    c, tok = client
    r = c.get("/api/knowledge", params={"limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert isinstance(body.get("items"), list)
