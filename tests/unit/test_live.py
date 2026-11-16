"""陪伴通道（neuromem/live.py）单测。

不打真实 LLM、不碰主库：用假 agent 注入，只验通道本身的契约 ——
空输入/关闭/忙锁、正常一轮的字段与回执、抛错后自愈、trace 追加。

这些行为是"web 上说的话真被记住"的地基，改坏了必须立刻红。
"""
import json

import pytest

import neuromem.live as live


class FakeAgent:
    """最小替身：返回 CompanionAgent.step() 同构的 row。"""

    def __init__(self, row=None, boom=False):
        self.row = row or {}
        self.boom = boom
        self.calls = []
        self.closed = 0

    def step(self, text):
        self.calls.append(text)
        if self.boom:
            raise RuntimeError("simulated brain failure")
        return dict(self.row, user=text)

    def close(self):
        self.closed += 1


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """每个用例：干净的单例、独立 trace 路径、默认开启。"""
    monkeypatch.setattr(live, "_agent", None)
    monkeypatch.setattr(live, "TRACE_PATH", tmp_path / "trace.jsonl")
    monkeypatch.setattr(live, "ENABLED", True)
    monkeypatch.setattr(live, "_stats",
                        {"turns": 0, "errors": 0, "last_at": None,
                         "last_ms": 0, "born_at": None})
    yield


def _row(**kw):
    base = {
        "reply": "记得，你上周去了青岛。",
        "remember": ["我上周去了青岛，吃了鲅鱼饺子"],
        "remembered_ids": [11],
        "used_ids": ["e1"],
        "candidate_count": 3,
        "outcome": "answered",
        "parse_ok": True,
        "fact_guard": True,
        "fact_violation": False,
        "fact_rewritten": False,
        "fact_trimmed": False,
        "remember_fallback": False,
        "mood_top": "期待",
        "elapsed_ms": 1200,
        "turn": 1,
    }
    base.update(kw)
    return base


def test_empty_input_rejected():
    r = live.turn("   ")
    assert r["ok"] is False and r["error"] == "empty"


def test_disabled_returns_hint(monkeypatch):
    monkeypatch.setattr(live, "ENABLED", False)
    r = live.turn("你好")
    assert r["ok"] is False and r["error"] == "disabled"
    assert "只读" in r["hint"]


def test_normal_turn_carries_memory_receipt(monkeypatch):
    fake = FakeAgent(_row())
    monkeypatch.setattr(live, "_spawn", lambda: fake)
    r = live.turn("我上周去了青岛")
    assert r["ok"] is True
    assert r["reply"] == "记得，你上周去了青岛。"
    assert r["remembered"] == ["我上周去了青岛，吃了鲅鱼饺子"]
    assert r["remembered_count"] == 1
    assert r["used"] == 1
    assert "写入记忆 1 条" in r["hint"] and "引用旧记忆 1 条" in r["hint"]
    assert fake.calls == ["我上周去了青岛"]
    assert live._stats["turns"] == 1


def test_trace_appended(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "_spawn", lambda: FakeAgent(_row()))
    live.turn("记一下：我养了只猫叫团子")
    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["user"] == "记一下：我养了只猫叫团子"
    assert rec["remember"] == ["我上周去了青岛，吃了鲅鱼饺子"]


def test_failure_heals_and_rebuilds(monkeypatch):
    bad = FakeAgent(boom=True)
    calls = {"n": 0}

    def spawn():
        calls["n"] += 1
        return bad if calls["n"] == 1 else FakeAgent(_row())

    monkeypatch.setattr(live, "_spawn", spawn)
    r1 = live.turn("第一句")
    assert r1["ok"] is False and "simulated brain failure" in r1["error"]
    assert live._agent is None and live._stats["errors"] == 1

    r2 = live.turn("第二句")          # 自愈：下一轮重建后正常
    assert r2["ok"] is True
    assert calls["n"] == 2


def test_busy_when_lock_held(monkeypatch):
    monkeypatch.setattr(live, "_spawn", lambda: FakeAgent(_row()))
    live._lock.acquire()
    try:
        monkeypatch.setattr(live, "_lock", live._lock)   # 同一把锁
        r = live.turn("排队看看")
        assert r["ok"] is False and r["error"] == "busy"
    finally:
        live._lock.release()


def test_hint_marks_guard_and_fallback():
    row = _row(remembered_ids=[], remember=["某条兜底"], remember_fallback=True,
               fact_violation=True, fact_rewritten=True, used_ids=[])
    hint = live._hint(row)
    assert "兜底提取" in hint and "事实护栏介入（已重写）" in hint


def test_input_truncated(monkeypatch):
    fake = FakeAgent(_row())
    monkeypatch.setattr(live, "_spawn", lambda: fake)
    live.turn("啊" * 5000)
    assert len(fake.calls[0]) == live.MAX_INPUT


# ---------------- 2026-11-16 上下文窗口：重启后接得上 ----------------
def test_history_warmup_reads_back_from_db(tmp_path):
    """进程重启后 deque 是空的，但原话一直在 conversation_turns 里。

    旧版 max_history=4 且不回填：web 常驻单例重启一次，上一句说过什么
    就彻底看不见了（事件层又是有损的）。这里锁住「重启能接上」。
    """
    from agent.companion import CompanionAgent
    from neuromem import db as nmdb

    path = tmp_path / "warm.db"
    conn = nmdb.connect(str(path))
    for i, (who, txt) in enumerate([
            ("你", "我在做记忆系统的召回层"),
            ("agent", "听起来挺费神的"),
            ("你", "卡在原话层没有索引")]):
        conn.execute(
            "INSERT INTO conversation_turns(id,session_id,ts,speaker,text,created_at)"
            " VALUES(?,?,?,?,?,?)",
            ("t%d" % i, "live_web", "2026-11-16T10:0%d:00+08:00" % i, who, txt,
             "2026-11-16T10:0%d:00+08:00" % i))
    conn.commit()
    conn.close()

    a = CompanionAgent(db_path=path, session_id="live_web",
                       llm_fn=lambda p: '{"reply":"ok"}', trace_path=None)
    try:
        assert a.history_warmed == 3
        assert [h["text"] for h in a.history][-1] == "卡在原话层没有索引"
        # agent 那一侧的角色名不能写成用户
        assert a.history[1]["role"] == "agent"
        assert a.history[0]["role"] == a.counterpart
    finally:
        a.brain.close()


def test_history_warmup_skipped_for_control_group(tmp_path):
    """对照组没有记忆，就不该回填——否则 A/B 变量不干净。"""
    from agent.companion import CompanionAgent
    from neuromem import db as nmdb

    path = tmp_path / "warm2.db"
    conn = nmdb.connect(str(path))
    conn.execute(
        "INSERT INTO conversation_turns(id,session_id,ts,speaker,text,created_at)"
        " VALUES(?,?,?,?,?,?)",
        ("t0", "live_web", "2026-11-16T10:00:00+08:00", "你", "随便一句",
         "2026-11-16T10:00:00+08:00"))
    conn.commit()
    conn.close()

    b = CompanionAgent(db_path=path, session_id="live_web", use_brain=False,
                       llm_fn=lambda p: '{"reply":"ok"}', trace_path=None)
    assert len(b.history) == 0


def test_history_window_covers_more_than_two_turns(monkeypatch):
    """窗口条数按轮可配；旧版固定 4 条 = 只有 2 轮。"""
    import importlib
    import agent.companion as cp
    monkeypatch.setenv("NEUROMEM_HISTORY_TURNS", "8")
    try:
        m = importlib.reload(cp)
        assert m.HISTORY_MAXLEN == 16
    finally:
        monkeypatch.delenv("NEUROMEM_HISTORY_TURNS", raising=False)
        importlib.reload(cp)


def test_history_text_marks_stale_conversation():
    """隔了很久还接着上一轮的语气说，是最典型的出戏点。"""
    from datetime import datetime, timedelta
    from agent import prompts

    now = datetime.now().astimezone()
    fresh = [{"role": "你", "text": "刚说的",
              "ts": (now - timedelta(minutes=2)).isoformat()}]
    old = [{"role": "你", "text": "两天前说的",
            "ts": (now - timedelta(days=2)).isoformat()}]

    assert "不是刚刚发生的" not in prompts._history_text(fresh)
    out = prompts._history_text(old)
    assert "不是刚刚发生的" in out and "两天多前" in out
    # 没有时间戳的旧数据不能因此崩掉
    assert prompts._history_text([{"role": "你", "text": "x"}]) == "你：x"


# ---------------- 2026-11-16 上下文窗口：重启后接得上 ----------------
def test_history_warmup_reads_back_from_db(tmp_path):
    """进程重启后 deque 是空的，但原话一直在 conversation_turns 里。

    旧版 max_history=4 且不回填：web 常驻单例重启一次，上一句说过什么
    就彻底看不见了（事件层又是有损的）。这里锁住「重启能接上」。
    """
    from agent.companion import CompanionAgent
    from neuromem import db as nmdb

    path = tmp_path / "warm.db"
    conn = nmdb.connect(str(path))
    for i, (who, txt) in enumerate([
            ("你", "我在做记忆系统的召回层"),
            ("agent", "听起来挺费神的"),
            ("你", "卡在原话层没有索引")]):
        conn.execute(
            "INSERT INTO conversation_turns(id,session_id,ts,speaker,text,created_at)"
            " VALUES(?,?,?,?,?,?)",
            ("t%d" % i, "live_web", "2026-11-16T10:0%d:00+08:00" % i, who, txt,
             "2026-11-16T10:0%d:00+08:00" % i))
    conn.commit()
    conn.close()

    a = CompanionAgent(db_path=path, session_id="live_web",
                       llm_fn=lambda p: '{"reply":"ok"}', trace_path=None)
    try:
        assert a.history_warmed == 3
        assert [h["text"] for h in a.history][-1] == "卡在原话层没有索引"
        # agent 那一侧的角色名不能写成用户
        assert a.history[1]["role"] == "agent"
        assert a.history[0]["role"] == a.counterpart
    finally:
        a.brain.close()


def test_history_warmup_skipped_for_control_group(tmp_path):
    """对照组没有记忆，就不该回填——否则 A/B 变量不干净。"""
    from agent.companion import CompanionAgent
    from neuromem import db as nmdb

    path = tmp_path / "warm2.db"
    conn = nmdb.connect(str(path))
    conn.execute(
        "INSERT INTO conversation_turns(id,session_id,ts,speaker,text,created_at)"
        " VALUES(?,?,?,?,?,?)",
        ("t0", "live_web", "2026-11-16T10:00:00+08:00", "你", "随便一句",
         "2026-11-16T10:00:00+08:00"))
    conn.commit()
    conn.close()

    b = CompanionAgent(db_path=path, session_id="live_web", use_brain=False,
                       llm_fn=lambda p: '{"reply":"ok"}', trace_path=None)
    assert len(b.history) == 0


def test_history_window_covers_more_than_two_turns(monkeypatch):
    """窗口条数按轮可配；旧版固定 4 条 = 只有 2 轮。"""
    import importlib
    import agent.companion as cp
    monkeypatch.setenv("NEUROMEM_HISTORY_TURNS", "8")
    try:
        m = importlib.reload(cp)
        assert m.HISTORY_MAXLEN == 16
    finally:
        monkeypatch.delenv("NEUROMEM_HISTORY_TURNS", raising=False)
        importlib.reload(cp)


def test_history_text_marks_stale_conversation():
    """隔了很久还接着上一轮的语气说，是最典型的出戏点。"""
    from datetime import datetime, timedelta
    from agent import prompts

    now = datetime.now().astimezone()
    fresh = [{"role": "你", "text": "刚说的",
              "ts": (now - timedelta(minutes=2)).isoformat()}]
    old = [{"role": "你", "text": "两天前说的",
            "ts": (now - timedelta(days=2)).isoformat()}]

    assert "不是刚刚发生的" not in prompts._history_text(fresh)
    out = prompts._history_text(old)
    assert "不是刚刚发生的" in out and "两天多前" in out
    # 没有时间戳的旧数据不能因此崩掉
    assert prompts._history_text([{"role": "你", "text": "x"}]) == "你：x"


# ---------------- 2026-11-16 跨轮复读 + 上一句指代 ----------------
def test_repeat_detection_covers_older_turns():
    """只比「上一轮」会漏掉跨轮复读：实测 11:38 与 11:49 两轮一字不差，
    中间隔了一轮不同的话，按上一条比对完全没触发。"""
    from agent.companion import _recent_agent_replies, _norm

    history = [
        {"role": "你", "text": "我上一句跟你说的是什么"},
        {"role": "agent", "text": "你刚说今天上午在修记忆写入不敏感的问题，我正想问搞定了没呢。"},
        {"role": "你", "text": "我最近在进行什么项目，简单介绍一下"},
        {"role": "agent", "text": "哎，你刚说在修记忆写入不敏感的问题，搞定了没？"},
        {"role": "你", "text": "我上一句说了什么"},
    ]
    seen = {_norm(x) for x in _recent_agent_replies(history, 3)}
    # 11:38 那句在最近 3 轮里 → 复读它必须被判定命中
    assert _norm("你刚说今天上午在修记忆写入不敏感的问题，我正想问搞定了没呢。") in seen
    assert len(_recent_agent_replies(history, 3)) == 2


def test_last_utterance_guard_pins_the_right_sentence(tmp_path):
    """「我上一句说什么」答案唯一，程序要钉死，不能让模型从窗口里挑。"""
    from agent.companion import (_is_last_utterance_query, _last_user_utterance)

    history = [
        {"role": "用户", "text": "我在做记忆系统"},
        {"role": "agent", "text": "听起来挺费神"},
        {"role": "用户", "text": "我最近在进行什么项目"},
        {"role": "agent", "text": "哎，你刚说在做记忆系统"},
    ]
    assert _is_last_utterance_query("我上一句说了什么")
    assert _is_last_utterance_query("我上一句话是什么")
    assert not _is_last_utterance_query("我最近在进行什么项目，简单介绍一下")
    assert not _is_last_utterance_query("我今天上午在干什么")
    assert _last_user_utterance(history, "用户") == "我最近在进行什么项目"


# ---------------- 2026-11-16 跨轮复读 + 上一句指代 ----------------
def test_repeat_detection_covers_older_turns():
    """只比「上一轮」会漏掉跨轮复读：实测 11:38 与 11:49 两轮一字不差，
    中间隔了一轮不同的话，按上一条比对完全没触发。"""
    from agent.companion import _recent_agent_replies, _norm

    history = [
        {"role": "你", "text": "我上一句跟你说的是什么"},
        {"role": "agent", "text": "你刚说今天上午在修记忆写入不敏感的问题，我正想问搞定了没呢。"},
        {"role": "你", "text": "我最近在进行什么项目，简单介绍一下"},
        {"role": "agent", "text": "哎，你刚说在修记忆写入不敏感的问题，搞定了没？"},
        {"role": "你", "text": "我上一句说了什么"},
    ]
    seen = {_norm(x) for x in _recent_agent_replies(history, 3)}
    # 11:38 那句在最近 3 轮里 → 复读它必须被判定命中
    assert _norm("你刚说今天上午在修记忆写入不敏感的问题，我正想问搞定了没呢。") in seen
    assert len(_recent_agent_replies(history, 3)) == 2


def test_last_utterance_guard_pins_the_right_sentence(tmp_path):
    """「我上一句说什么」答案唯一，程序要钉死，不能让模型从窗口里挑。"""
    from agent.companion import (_is_last_utterance_query, _last_user_utterance)

    history = [
        {"role": "用户", "text": "我在做记忆系统"},
        {"role": "agent", "text": "听起来挺费神"},
        {"role": "用户", "text": "我最近在进行什么项目"},
        {"role": "agent", "text": "哎，你刚说在做记忆系统"},
    ]
    assert _is_last_utterance_query("我上一句说了什么")
    assert _is_last_utterance_query("我上一句话是什么")
    assert not _is_last_utterance_query("我最近在进行什么项目，简单介绍一下")
    assert not _is_last_utterance_query("我今天上午在干什么")
    assert _last_user_utterance(history, "用户") == "我最近在进行什么项目"
