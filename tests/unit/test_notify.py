"""「她回复了」系统通知的契约测试。

覆盖三件事：
- 通知主路径是可点击 Toast，失败降级不抛；
- 点击回调会把窗口带回聊天页（在聊天页只聚焦、不重载）；
- 前端确实在文字+语音就绪后才调用通知桥接。

不打真实通知、不碰主库。
"""
import time

import pytest

import desktop.app as da


@pytest.fixture(autouse=True)
def _restore_notify_state():
    """每个用例前后还原模块级状态，避免测试间互相污染。"""
    saved_ref = dict(da._WINDOW_REF)
    saved_broken = notify._broken
    yield
    da._WINDOW_REF.clear()
    da._WINDOW_REF.update(saved_ref)
    notify._broken = saved_broken
import desktop.notify as notify


def test_send_falls_back_when_toast_missing(monkeypatch):
    """没装 win11toast（或非 Windows）时降级气球，不抛异常。"""
    calls = []

    def no_toast():
        raise ImportError("no win11toast")

    monkeypatch.setattr(notify, "_load_toast", no_toast)
    monkeypatch.setattr(notify, "_balloon", lambda t, m: calls.append((t, m)) or False)
    notify._broken = False
    # send 立即返回 True 表示已投递；降级在守护线程内发生
    assert notify.send("阿澈", "在吗") is True
    # 等守护线程真正跑完降级分支（_broken 被置 True），避免线程跨用例污染
    for _ in range(100):
        if notify._broken:
            break
        time.sleep(0.02)
    assert notify._broken is True
    assert calls == [("阿澈", "在吗")]


def test_send_registers_click_callback(monkeypatch):
    """可点击 Toast：点击时触发回调；发送本身在守护线程，不阻塞调用方。"""
    got = {}

    def fake_toast(title, body, **kwargs):
        got["title"], got["body"] = title, body
        kwargs["on_click"]({"arguments": ""})   # 模拟用户点击

    # _load_toast 是无参加载器，返回发送函数
    monkeypatch.setattr(notify, "_load_toast", lambda: fake_toast)
    notify._broken = False
    clicked = []
    assert notify.send("阿澈", "我在这儿", lambda: clicked.append(1)) is True
    for _ in range(50):
        if got:
            break
        time.sleep(0.02)
    assert got["title"] == "阿澈"
    assert got["body"] == "我在这儿"
    assert clicked == [1]


def test_notify_reply_wires_focus_callback(monkeypatch):
    """回复通知必须把「回聊天页」作为点击回调传下去。"""
    sent = {}

    def fake_send(title, message, on_click=None):
        sent.update(title=title, message=message, on_click=on_click)
        return True

    monkeypatch.setattr(notify, "send", fake_send)
    api = da._Api(18778)
    api.notify_reply("阿澈", "今天过得怎么样")
    assert sent["title"] == "阿澈"
    assert sent["message"] == "今天过得怎么样"
    assert callable(sent["on_click"])
    assert sent["on_click"] == api._focus_chat


def test_js_api_has_no_recursive_attributes():
    """回归锁：js_api 实例不能含会被 pywebview dir() 递归展开的复杂属性。

    v31 曾把 webview.Window 塞进 api.window，pywebview 反射时无限递归
    （pty.Empty.Empty... → RecursionError），窗口卡死在「未响应」。
    这里复刻 pywebview 的 get_functions 展开规则：不可调用 + 带 __module__
    的对象会被递归——桥接实例上绝不能出现这种属性。
    """
    import inspect

    api = da._Api(18778)
    for name in dir(api):
        if name.startswith("_"):
            continue
        attr = getattr(api, name)
        if inspect.ismethod(attr) or inspect.isfunction(attr):
            continue
        # pywebview 会对下面这类属性递归展开，桥接对象上不允许存在
        assert not (not callable(attr) and hasattr(attr, "__module__")), \
            f"js_api 属性 {name} 会被 pywebview 递归展开，导致窗口未响应"

    # 窗口引用必须放在模块级，且不在实例 __dict__ 里
    assert "window" not in api.__dict__
    assert "chat_url" not in api.__dict__
    assert "window" in da._WINDOW_REF


def test_focus_chat_no_window_is_safe():
    """窗口还没创建好就被点到：静默返回，不能把通知线程搞崩。"""
    api = da._Api(18778)
    assert api._focus_chat() is None


def test_focus_chat_on_chat_page_only_focuses():
    """已经在聊天页：只聚焦输入框，不重载，保住没发出去的草稿。"""
    api = da._Api(18778)

    class Win:
        def __init__(self):
            self.calls = []

        def restore(self):
            self.calls.append("restore")

        def show(self):
            self.calls.append("show")

        def evaluate_js(self, js):
            self.calls.append("eval")
            return True if "!!document" in js else None

        def load_url(self, url):
            self.calls.append("load:" + url)

    w = Win()
    da._WINDOW_REF["window"] = w
    da._WINDOW_REF["chat_url"] = "http://127.0.0.1:18778/desk/index.html"
    api._focus_chat()
    assert "restore" in w.calls and "show" in w.calls
    assert not any(c.startswith("load:") for c in w.calls)


def test_focus_chat_from_other_page_loads_chat():
    """从设置页等其它页面点通知：加载聊天页并带 from=notify 标记。"""
    api = da._Api(18778)

    class Win:
        def __init__(self):
            self.calls = []

        def restore(self):
            self.calls.append("restore")

        def show(self):
            self.calls.append("show")

        def evaluate_js(self, js):
            self.calls.append("eval")
            return False if "!!document" in js else None

        def load_url(self, url):
            self.calls.append("load:" + url)

    w = Win()
    da._WINDOW_REF["window"] = w
    da._WINDOW_REF["chat_url"] = "http://127.0.0.1:18778/desk/index.html"
    api._focus_chat()
    assert any(c.startswith("load:") and "index.html?from=notify" in c
               for c in w.calls)


def test_frontend_wires_voice_ready_then_notify():
    """供给的 JS 必须：等 ttsEnsure 成功后才 notify_reply。"""
    from fastapi.testclient import TestClient
    from neuromem import api as core_api

    da._build_server(18780)
    c = TestClient(da._DesktopAuth(core_api.app, da._WEB_LOGIN_TOKEN["value"]))
    logic = c.get("/desk/logic.js").text
    app = c.get("/desk/assets/app.js").text
    assert "notifyReply" in logic and "notify_reply" in logic
    assert "fireReplyNotice" in app
    assert "ttsEnsure(text, voice" in app          # 语音就绪门闩
    assert "replyNoticeSeq" in app                 # 防旧通知迟到
    assert "from=notify" in app                    # 回跳后聚焦输入框
