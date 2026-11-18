"""「她回复了」系统通知的契约测试。

覆盖三件事：
- 通知主路径是可点击 Toast，失败降级不抛；
- 点击回调会把窗口带回聊天页（在聊天页只聚焦、不重载）；
- 前端确实在文字+语音就绪后才调用通知桥接。

不打真实通知、不碰主库。
"""
import time

import desktop.app as da
import desktop.notify as notify


def test_send_falls_back_when_toast_missing(monkeypatch):
    """没装 win11toast（或非 Windows）时降级气球，不抛异常。"""
    calls = []
    monkeypatch.setattr(notify, "_toast", None)
    monkeypatch.setattr(notify, "_balloon", lambda t, m: calls.append((t, m)) or False)
    assert notify.send("阿澈", "在吗") is False
    assert calls == [("阿澈", "在吗")]


def test_send_registers_click_callback(monkeypatch):
    """可点击 Toast：点击时触发回调；发送本身在守护线程，不阻塞调用方。"""
    got = {}

    def fake_toast(title, body, **kwargs):
        got["title"], got["body"] = title, body
        kwargs["on_click"]({"arguments": ""})   # 模拟用户点击

    monkeypatch.setattr(notify, "_toast", fake_toast)
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
    assert sent["on_click"].__func__ == api._focus_chat.__func__


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
    api.window = w
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
    api.window = w
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
