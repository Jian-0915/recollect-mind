"""陪伴 Agent 实验台离线回归：注入 fake LLM，不联网、不碰主库。

重点验的是「agent 自己判断 + 回传」这条链路的可信度，而不是模型文采：
伪造 id 必须被拦、解析失败必须降级、对照模式绝不写库、报告指标可计算。
"""
import json

import pytest

from agent import companion as ag
from agent.companion import (CompanionAgent, clean_reply, normalize_remember,
                            parse_output)
from neuromem import config, db, identity, store
from neuromem.brain import Brain


@pytest.fixture()
def offline(monkeypatch):
    dim = config.EMBED_DIM
    monkeypatch.setattr(store.embed, "embed",
                        lambda t, *a, **k: [1.0] + [0.0] * (dim - 1))
    monkeypatch.setattr(store.persona, "extract_relations", lambda *a, **k: [])
    monkeypatch.setattr(store.llm, "generate", lambda *a, **k: "（离线自述）")
    monkeypatch.setattr(config, "DUP_SIM", 2.0)
    monkeypatch.setattr(config, "SUPERSEDE_SIM", 2.0)
    return monkeypatch


def _fake(out):
    return lambda prompt: json.dumps(out, ensure_ascii=False)


def test_parse_and_clean():
    d, ok = parse_output('```json\n{"reply":"好呀","used_ids":[]}\n```')
    assert ok and d["reply"] == "好呀"
    d, ok = parse_output('抱歉，我不会输出 JSON')
    assert not ok and d["outcome"] == "refused"
    d, ok = parse_output('前缀 {"reply":"[id:evt_a] [关于对方] 你叫小林"} 后缀')
    assert ok
    assert clean_reply(d["reply"]) == "你叫小林"


def test_normalize_remember_filters_noise():
    raw = ["我叫小林，在杭州做设计",
           "[id:evt_x] 这是工具痕迹",
           "我这边没组织好",  # 与 reply 相同
           {"text": "我们说好每月看一次电影", "subject": "shared"},
           "根据候选记忆推测对方喜欢猫"]  # 含「候选」
    got = normalize_remember(raw, "我这边没组织好")
    assert [t for t, _ in got] == ["我叫小林，在杭州做设计",
                                   "我们说好每月看一次电影"]
    assert got[1][1] == "shared"


def test_step_end_to_end_writes_real_l2(memdb, conn, offline):
    ids = store.remember(conn, "我叫小林，在杭州做平面设计", speaker="用户")
    eid = ids[0]
    out = {
        "reply": "你叫小林，在杭州做平面设计，对吧？",
        "used_ids": [eid],
        "context_text": "你叫小林，在杭州做平面设计",
        "remember": ["我养了只猫叫年糕"],
        "affect": {"joy": 0.4}, "topic": "自我介绍",
        "open_topic": "猫", "outcome": "answered",
    }
    a = CompanionAgent(db_path=memdb, llm_fn=_fake(out), trace_path=None)
    try:
        # 2026-11-16：remember 现在必须在对方本轮原话里有字面依据（否则模型会把
        # 候选里的旧事当新事实再交一遍）。所以原话里要真带上这条事实。
        row = a.step("我叫什么？对了，我养了只猫叫年糕")
        assert row["reply"] == out["reply"]
        assert row["used_ids"] == [eid]
        assert row["context_ok"] and row["parse_ok"]
        assert len(row["remembered_ids"]) >= 1, "agent 应自己沉淀新事实"
        assert "[id:" not in row["reply"]
    finally:
        a.close()

    c = db.connect(memdb)
    try:
        assert c.execute("SELECT COUNT(*) FROM adoption_log "
                         "WHERE source='agent'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM adoption_log WHERE source='agent' "
                         "AND summary!=''").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM affect_events").fetchone()[0] >= 1
        assert c.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0] >= 1
    finally:
        c.close()


def test_forged_used_ids_are_rejected(memdb, conn, offline):
    a = CompanionAgent(db_path=memdb, llm_fn=_fake({
        "reply": "我记得你叫小林", "used_ids": ["evt_不存在", "evt_伪造"],
        "context_text": "你叫小林", "outcome": "answered"}), trace_path=None)
    try:
        row = a.step("我叫什么？")
        assert row["used_ids"] == []
        assert len(row["invalid_used_ids"]) == 2
    finally:
        a.close()
    c = db.connect(memdb)
    try:
        r = c.execute("SELECT used_ids FROM adoption_log "
                      "WHERE source='agent'").fetchone()
        assert r and r["used_ids"] in ("", "[]", None)
    finally:
        c.close()


def test_parse_failure_does_not_crash(memdb, offline):
    a = CompanionAgent(db_path=memdb,
                       llm_fn=lambda p: "我不会按格式输出", trace_path=None)
    try:
        row = a.step("随便问一句")
        assert not row["parse_ok"] and row["outcome"] == "refused"
        assert "{" not in row["reply"]
        assert row["used_ids"] == []
    finally:
        a.close()


def test_no_brain_control_writes_nothing(memdb, offline):
    before = db.connect(memdb)
    t0 = before.execute("SELECT COUNT(*) FROM adoption_log").fetchone()[0]
    before.close()
    a = CompanionAgent(db_path=memdb, use_brain=False, llm_fn=_fake({
        "reply": "你好呀", "outcome": "answered"}), trace_path=None)
    try:
        row = a.step("嗨")
        assert row["reply"] == "你好呀" and row["candidate_count"] == 0
        rep = a.report()
        assert rep["db_state"]["adoption_agent"] == t0
        assert rep["behavior"]["turns"] == 1
    finally:
        a.close()


def test_report_behavior_metrics(memdb, offline):
    a = CompanionAgent(db_path=memdb, llm_fn=_fake({
        "reply": "嗯", "used_ids": [], "context_text": "",
        "outcome": "refused"}), trace_path=None)
    try:
        a.step("第一句")
        a.step("第二句")
        rep = a.report()
        assert rep["behavior"]["turns"] == 2
        assert rep["behavior"]["refused_rate"] == 1.0
        assert rep["behavior"]["repeat_rate"] == 1.0   # 两句完全相同 → 复读
        assert rep["behavior"]["label_leak_turns"] == 0
    finally:
        a.close()


def test_fresh_guard_refuses_main_db(monkeypatch):
    from agent import __main__ as cli
    import sys
    with pytest.raises(SystemExit) as e:
        cli._fresh([cli.MAIN_DB], True)
    assert "拒绝清空主库" in str(e.value)


def test_identity_is_readonly_for_agent(memdb, offline):
    """Brain 没有 set_identity，agent 也不该偷偷长出一个。"""
    b = Brain(path=memdb)
    try:
        assert not hasattr(b, "set_identity")
    finally:
        b.close()
    a = CompanionAgent(db_path=memdb, llm_fn=_fake({"reply": "好"}),
                        trace_path=None)
    try:
        assert not hasattr(a, "set_identity")
        assert identity.verify(a.brain.conn)[0]
    finally:
        a.close()
