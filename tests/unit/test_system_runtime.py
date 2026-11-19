"""v47：系统运行界面——只读聚合快照 + 页面入口。

覆盖：快照字段齐全、不消费播报（GET 无副作用）、各子系统异常隔离、
页面入口存在、接口在桌面端与网页后端都注册。
"""
from pathlib import Path

from neuromem import autoresearch as ar
from neuromem import knowledge, runtime

ROOT = Path(__file__).resolve().parents[2]


class _NoClose:
    """生产代码自己开自己关；这里保住测试连接可读（项目既有模式）。"""
    def __init__(self, conn):
        self._c = conn

    def __getattr__(self, name):
        return getattr(self._c, name)

    def close(self):
        pass


def _add_brief(kb, text="我自己查到了点东西", delivered=0):
    kb.execute(
        "INSERT INTO discovery_brief(entity,text,sources,delivered,created_at) "
        "VALUES(?,?,?,?,?)", ("测试", text, "[]", delivered, "2026-11-19T12:00:00+08:00"))
    kb.commit()


# --- 快照结构 -------------------------------------------------------------

def test_snapshot_has_all_sections():
    s = runtime.snapshot()
    assert s["ok"] is True
    for key in ("autoresearch", "knowledge", "pipeline", "chat", "now"):
        assert key in s, key
    assert s["pipeline"]["stage"] in ("off", "queued", "idle", "watching")


def test_snapshot_returns_queues_and_topics(tmp_path, monkeypatch):
    real = knowledge.connect(tmp_path / "kb.db")
    kb = _NoClose(real)
    monkeypatch.setattr(knowledge, "connect", lambda path=None: kb)
    knowledge.enqueue_research(kb, entity="贵州景点", reason="她答应去查")
    _add_brief(kb)
    s = runtime.snapshot()
    k = s["knowledge"]
    assert any(t["entity"] == "贵州景点" for t in k["backlog"])
    assert any(b["text"] == "我自己查到了点东西" for b in k["briefs"])
    assert k["backlog_stats"]["pending"] >= 1
    kb.close()


# --- 铁律：GET 绝不能消费播报 ---------------------------------------------

def test_snapshot_does_not_consume_brief(tmp_path, monkeypatch):
    """监控页只读——不能像 /api/autoresearch/brief 那样把消息偷走。"""
    real = knowledge.connect(tmp_path / "kb.db")
    kb = _NoClose(real)
    monkeypatch.setattr(knowledge, "connect", lambda path=None: kb)
    _add_brief(kb)
    assert knowledge.brief_stats(kb)["pending"] == 1
    runtime.snapshot()
    runtime.snapshot()
    assert knowledge.brief_stats(kb)["pending"] == 1   # 调两次仍待播报


def test_snapshot_does_not_change_backlog(tmp_path, monkeypatch):
    real = knowledge.connect(tmp_path / "kb.db")
    kb = _NoClose(real)
    monkeypatch.setattr(knowledge, "connect", lambda path=None: kb)
    knowledge.enqueue_research(kb, entity="茅台镇", reason="她答应去查")
    before = kb.execute("SELECT status,attempts FROM research_backlog").fetchall()
    runtime.snapshot()
    after = kb.execute("SELECT status,attempts FROM research_backlog").fetchall()
    assert before == after


# --- 异常隔离：一个子系统坏了，其它照常返回 --------------------------------

def test_snapshot_isolates_kb_failure(monkeypatch):
    def boom(path=None):
        raise RuntimeError("kb down")
    monkeypatch.setattr(knowledge, "connect", boom)
    s = runtime.snapshot()
    assert s["ok"] is True
    assert "error" in s["knowledge"]           # 知识库报错
    assert "autoresearch" in s and "chat" in s  # 其它区仍在


# --- 页面与路由 -------------------------------------------------------------

def test_status_page_exists_and_has_api_call():
    page = ROOT / "desktop" / "web" / "status.html"
    assert page.exists()
    html = page.read_text(encoding="utf-8")
    assert "/api/system/runtime" in html
    assert "待查队列" in html and "聊天主题" in html
    assert "setInterval" in html               # 自动刷新


def test_chat_page_links_to_status():
    html = (ROOT / "desktop" / "web" / "index.html").read_text(encoding="utf-8")
    assert 'href="status.html"' in html


def test_status_page_content_is_scrollable():
    """独立状态页内容区必须可滚动：.app 是 overflow:hidden 的固定视口，
    内容容器若不设 overflow-y:auto，内容超出就被裁掉滑不动（2026-11-19 实测）。"""
    html = (ROOT / "desktop" / "web" / "status.html").read_text(encoding="utf-8")
    import re
    m = re.search(r"\.runtime\s*\{([^}]*)\}", html)
    assert m, "找不到 .runtime 样式规则"
    css = m.group(1)
    assert "overflow-y: auto" in css          # 关键：内容区自己滚
    assert "min-height: 0" in css             # flex 子项允许收缩，否则撑破视口
    assert "flex:" in css                     # 占满 .app 剩余高度


def test_runtime_route_registered():
    from neuromem.api import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/system/runtime" in paths
