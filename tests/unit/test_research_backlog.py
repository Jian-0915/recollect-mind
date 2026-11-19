"""v45：不走被动路径——她自己查资料（承诺兑现 + open_topic 补课）。

全部离线 fake provider，零网络、不碰主库：知识库走 tmp_path。
"""
import json

import pytest

from neuromem import (autoresearch as ar, config, intent, knowledge,
                      promise, sourceguard)


@pytest.fixture
def kb(tmp_path, monkeypatch):
    real = knowledge.connect(tmp_path / "kb.db")

    class _NoClose:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def close(self):
            pass   # 生产代码自己开自己关，这里保住测试连接

    c = _NoClose(real)
    monkeypatch.setattr(knowledge, "connect", lambda path=None: c)
    monkeypatch.setattr(config, "AUTORESEARCH", True)
    ar.set_enabled(True)
    yield real
    ar.set_enabled(False)
    real.close()


class FakeResult:
    def __init__(self, title, url, snippet):
        self.title, self.url, self.snippet = title, url, snippet
        self.request_id = "fake"


class FakeProvider:
    def __init__(self, hit=True):
        self.hit = hit
        self.queries = []

    def search(self, query, max_results=5):
        self.queries.append(query)
        if not self.hit:
            return []
        return [FakeResult("贵州景点攻略", "https://g.example", "黄果树瀑布介绍")]


# --- 承诺识别 -------------------------------------------------------------

def test_promise_detection():
    assert promise.has_promise(sourceguard._FALLBACK)
    assert promise.has_promise("这个我还真不太清楚，我去查一下再跟你说")
    assert not promise.has_promise("嗯，我觉得挺好的")
    assert not promise.has_promise("你去查查看吧")   # 提议，不是她自己的承诺


def test_promised_topic_extracts_and_sanitizes():
    topic = promise.promised_topic(
        "贵州有什么景点", sourceguard._FALLBACK, private_terms=("微微",))
    assert topic == "贵州景点"
    # 带称呼的问句不能把称呼带进检索词
    assert "微微" not in promise.promised_topic(
        "微微你知道乌江寨在哪吗", sourceguard._FALLBACK, ("微微",))


def test_promised_topic_empty_without_promise_or_private_leak():
    assert promise.promised_topic("贵州有什么景点", "好的呀", ()) == ""
    assert promise.promised_topic("我女朋友的住址在哪", sourceguard._FALLBACK, ()) == ""


# --- 入队 -----------------------------------------------------------------

def test_enqueue_promise_queues_topic(kb, monkeypatch):
    tid = ar.enqueue_promise("贵州有什么景点", sourceguard._FALLBACK, (), "turn-1")
    assert tid > 0
    pending = knowledge.pending_research(kb)
    assert pending[0]["entity"] == "贵州景点"
    assert pending[0]["reason"] == "她答应去查"
    assert pending[0]["source_event"] == "turn-1"


def test_enqueue_promise_dedups(kb):
    first = ar.enqueue_promise("贵州有什么景点", sourceguard._FALLBACK)
    second = ar.enqueue_promise("贵州有什么景点", sourceguard._FALLBACK)
    assert first > 0 and second == 0
    assert len(knowledge.pending_research(kb)) == 1


def test_enqueue_promise_noop_when_off(kb, monkeypatch):
    monkeypatch.setattr(config, "AUTORESEARCH", False)
    ar.set_enabled(False)
    assert ar.enqueue_promise("贵州有什么景点", sourceguard._FALLBACK) == 0
    assert knowledge.pending_research(kb) == []


# --- 循环兑现 -------------------------------------------------------------

def test_tick_once_fulfills_promise_and_writes_brief(kb, monkeypatch):
    monkeypatch.setattr(config, "AUTORESEARCH_TOPIC_SCAN", False)
    monkeypatch.setattr(ar, "should_search", lambda c, now=None: (True, "可以查"))
    ar.enqueue_promise("贵州有什么景点", sourceguard._FALLBACK)
    provider = FakeProvider()
    out = ar.tick_once(provider=provider, generate_fn=lambda p: "我自己查了黄果树瀑布。")
    assert out["searched"] == 1 and out["hits"] == 1
    assert knowledge.pending_research(kb) == []
    brief = knowledge.peek_brief(kb)
    assert brief and brief["entity"] == "贵州景点" and brief["sources"]
    assert "黄果树" in brief["text"]


def test_tick_once_marks_empty_search_done(kb, monkeypatch):
    monkeypatch.setattr(config, "AUTORESEARCH_TOPIC_SCAN", False)
    monkeypatch.setattr(ar, "should_search", lambda c, now=None: (True, "可以查"))
    ar.enqueue_promise("不存在的东西在哪里", sourceguard._FALLBACK)
    out = ar.tick_once(provider=FakeProvider(hit=False))
    assert out["searched"] == 1 and out["hits"] == 0
    assert knowledge.pending_research(kb) == []
    assert knowledge.peek_brief(kb) is None


def test_tick_once_respects_gate_and_keeps_pending(kb, monkeypatch):
    monkeypatch.setattr(config, "AUTORESEARCH_TOPIC_SCAN", False)
    monkeypatch.setattr(ar, "should_search", lambda c, now=None: (False, "配额不够"))
    ar.enqueue_promise("贵州有什么景点", sourceguard._FALLBACK)
    provider = FakeProvider()
    out = ar.tick_once(provider=provider)
    assert out["skipped"] == 1 and provider.queries == []
    # 闸门挡下就留在队列里，下次再试；不能把承诺弄丢
    assert len(knowledge.pending_research(kb)) == 1


def test_tick_once_logs_audit(kb, monkeypatch):
    monkeypatch.setattr(config, "AUTORESEARCH_TOPIC_SCAN", False)
    monkeypatch.setattr(ar, "should_search", lambda c, now=None: (True, "可以查"))
    ar.enqueue_promise("贵州有什么景点", sourceguard._FALLBACK, (), "turn-9")
    ar.tick_once(provider=FakeProvider())
    logs = knowledge.list_autoresearch(kb)
    assert logs and logs[0]["entity"] == "贵州景点"
    assert "查" in logs[0]["reason"] and logs[0]["source_event"] == "turn-9"


def test_status_exposes_backlog_and_loop(kb):
    st = ar.status(kb)
    assert "backlog" in st and "loop_alive" in st and "tick_min" in st
    assert st["enabled"] is True


# --- 红线：不写主库 -------------------------------------------------------

def test_enqueue_never_touches_main_db(kb, monkeypatch):
    """承诺队列只写独立库；主库连一张新表都不该多。"""
    from neuromem import db
    main_before = set(r[0] for r in db.connect().execute(
        "SELECT name FROM sqlite_master WHERE type='table'"))
    ar.enqueue_promise("贵州有什么景点", sourceguard._FALLBACK)
    main_after = set(r[0] for r in db.connect().execute(
        "SELECT name FROM sqlite_master WHERE type='table'"))
    assert main_before == main_after
    assert "research_backlog" not in main_after


# --- open_topic 补课 ------------------------------------------------------

def test_tick_scans_open_topics(kb, monkeypatch):
    monkeypatch.setattr(ar, "_open_topics", lambda: ["贵州有什么景点", "乌江寨在哪"])
    monkeypatch.setattr(ar, "should_search", lambda c, now=None: (False, "配额不够"))
    out = ar.tick_once(provider=FakeProvider())
    assert out["queued"] >= 1
    entities = [x["entity"] for x in knowledge.pending_research(kb)]
    assert "贵州景点" in entities and "乌江寨" in entities


def test_open_topic_scan_skips_private_and_known(kb, monkeypatch):
    class R:
        title, url, snippet, request_id = "乌江寨", "https://u.example", "介绍", "r"
    knowledge.add_results(kb, [R()], embed_text=False)
    monkeypatch.setattr(ar, "_open_topics",
                        lambda: ["我女朋友的住址在哪", "乌江寨在哪"])
    out = ar.tick_once(provider=FakeProvider())
    assert out["queued"] == 0


# --- v46 真实对话回归：「她答应了却一直没查」----------------------------

def test_real_reply_promises_are_recognized():
    """2026-11-19 实测她连说四轮的承诺话术，旧正则全都漏。"""
    real_replies = [
        "有呀～不过具体景点我得先查查才能给你准确的信息。你先说说想去哪种？",
        "两天刚好呀～那我按两天给你找条顺的自驾线。你稍等我查查，查到就发你～",
        "还在查呢～我得把开车时间捋清楚才能给你准信，查到就第一时间发你～",
        "开车自驾逛贵州，这个想法不错～不过我得先查查才能给你准信。",
    ]
    for r in real_replies:
        assert promise.has_promise(r), r


def test_real_spoken_questions_extract_topic():
    """真实口语问句必须能抽出主题，否则 F5 白跑。"""
    assert intent.extract_info_query("那有没有其他贵州的景点推荐呢") == "贵州景点"
    assert intent.extract_info_query("贵州好玩的地方有哪些") == "贵州好玩"


def test_promised_topic_backtracks_history_when_turn_is_short(kb):
    """本轮只回「就两天」，主题在上文——不回溯就排不进队列（本次根因）。"""
    hist = [
        {"role": "user", "text": "那有没有其他贵州的景点推荐呢"},
        {"role": "user", "text": "我想看适合开车游玩的地方，自己开车逛逛"},
        {"role": "user", "text": "就两天"},
    ]
    reply = "两天刚好呀～我按两天给你找条顺的自驾线。你稍等我查查，查到就发你～"
    tid = ar.enqueue_promise("就两天", reply, (), "turn-real", history=hist)
    assert tid > 0
    assert knowledge.pending_research(kb)[0]["entity"] == "贵州景点"


def test_promised_topic_short_turn_no_history_stays_empty(kb):
    """没有上文也没主题时宁可不查，不能拿「就两天」当检索词。"""
    reply = "你稍等我查查，查到就发你～"
    assert ar.enqueue_promise("就两天", reply, ()) == 0


def test_backlog_stats_breakdown(kb):
    """v48：待查/已查/失败分开统计，监控页显示 0/4 而不只是 0。"""
    knowledge.enqueue_research(kb, entity="主题甲")
    st = knowledge.pending_research(kb)
    knowledge.mark_research(kb, st[0]["id"], status="done")
    knowledge.enqueue_research(kb, entity="主题乙")
    stats = knowledge.backlog_stats(kb)
    assert stats["done"] >= 1 and stats["pending"] >= 1
    assert stats["total"] == stats["done"] + stats["pending"] + stats["failed"]
