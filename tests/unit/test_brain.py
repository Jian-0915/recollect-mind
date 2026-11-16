"""v13 主体翻转 / 皮层情感 / Brain / Cortex 的回归用例。

全部跑在临时库（tests/conftest.py 的 memdb），不碰主库；LLM / 嵌入统一
离线 mock，保证不依赖 Ollama 也能稳定复现。
"""
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from neuromem import (affect, answer, companion, config, cortex, identity,
                      selfnarrative, store)
from neuromem.brain import Brain, ContextPack


@pytest.fixture()
def offline(monkeypatch):
    dim = config.EMBED_DIM
    monkeypatch.setattr(store.embed, "embed",
                        lambda t, *a, **k: [1.0] + [0.0] * (dim - 1))
    monkeypatch.setattr(store.persona, "extract_relations", lambda *a, **k: [])
    monkeypatch.setattr(store.llm, "generate", lambda *a, **k: "（离线自述）")
    # 常量向量会让所有句子相似度=1，去重/取代闸门抬到不可能触发
    monkeypatch.setattr(config, "DUP_SIM", 2.0)
    monkeypatch.setattr(config, "SUPERSEDE_SIM", 2.0)
    return monkeypatch


def _event(conn, eid, gist, subject, created=None):
    conn.execute(
        "INSERT INTO events(id,created_at,gist,subject,embedding) "
        "VALUES(?,?,?,?,NULL)",
        (eid, created or datetime.now(timezone.utc).astimezone().isoformat(
            timespec="seconds"), gist, subject))
    conn.commit()


# ---------------- 阶段 1 遗留：主体翻转 ----------------
def test_subject_and_first_person_target():
    assert identity.subject_of("用户") == "counterpart"
    assert identity.subject_of("agent") == "self"
    assert identity.subject_of("用户", "shared") == "shared"
    assert identity.first_person_target("用户", "counterpart") == "用户"
    assert identity.first_person_target("用户", "self") == config.SELF_NAME
    assert identity.first_person_target("用户", "third_party") is None
    assert "回答里用「我」" in identity.perspective_hint("self")


def test_self_gist_is_not_rewritten_as_counterpart(memdb, conn, offline):
    a = store.remember(conn, "我答应过每周都来", speaker="用户", subject="self")[0]
    b = store.remember(conn, "我怕打雷", speaker="用户")[0]
    c = store.remember(conn, "我养了只猫", speaker="用户", subject="third_party")[0]
    d = store.remember(conn, "我下周再来", speaker="agent")[0]
    rows = {r["id"]: r for r in conn.execute("SELECT id,gist,subject FROM events")}
    assert rows[a]["gist"] == "我答应过每周都来", "agent 的第一人称不能被写成对方"
    assert rows[a]["subject"] == "self"
    assert rows[b]["gist"] == "用户怕打雷" and rows[b]["subject"] == "counterpart"
    assert rows[c]["gist"] == "我养了只猫" and rows[c]["subject"] == "third_party"
    assert rows[d]["gist"] == "我下周再来" and rows[d]["subject"] == "self"


def test_legacy_gist_contract_still_holds(memdb, conn, offline):
    """旧行为必须兼容：speaker='用户' 时「我」仍替换，且「我们」不被破坏。"""
    eid = store.remember(
        conn, "我和小明是好朋友，我们认识很久了", speaker="用户")[0]
    gist = conn.execute("SELECT gist FROM events WHERE id=?", (eid,)).fetchone()[0]
    assert "用户和小明是好朋友" in gist and "用户们" not in gist


def test_selfnarrative_subject_comes_from_l0(memdb, conn, offline):
    prompts = []
    offline.setattr(selfnarrative.llm, "generate",
                    lambda p, **k: (prompts.append(p) or "（离线自述）"))
    identity.set_profile(conn, {"who": "小伴，一个记事的陪伴者"},
                         actor="human", reason="单测")
    _event(conn, "evt_self_1", "我答应过每天都在这里", "self")
    text = selfnarrative.build(conn)
    assert text
    assert "小伴" in prompts[-1], "自述主体必须由 L0 给定，不能让 LLM 猜"
    assert "陪伴者自己" in prompts[-1]
    row = conn.execute("SELECT scope FROM self_narrative WHERE id=1").fetchone()
    assert row["scope"] == "self"


def test_legacy_narrative_is_marked_not_self(memdb, conn, offline):
    """全 counterpart 的老库：允许回退但必须标 scope，不能当成 agent 人格。"""
    _event(conn, "evt_old_1", "用户去年去了海边", "counterpart")
    conn.execute("INSERT INTO personas(id,canonical_name,profile) "
                "VALUES('p1','用户','喜欢海边')")
    conn.execute("INSERT INTO edges(source,target,type,weight) "
                "VALUES('evt_old_1','p1','involves',1.0)")
    conn.commit()
    text = selfnarrative.build(conn)
    assert text is None or conn.execute(
        "SELECT scope FROM self_narrative WHERE id=1").fetchone()[0] == "counterpart"
    st = companion.state(conn)
    block = companion.format_block(st)
    assert "他眼里的自己" not in block, "不能再把对方自述写成 agent 的自我认知"
    assert "不是我" in block or "关于对方" in block or "（无）" == block


def test_companion_block_uses_first_person_view(memdb, conn):
    st = companion.state(conn)
    st["profile"] = "喜欢雨天"
    st["narrative"] = "我陪他聊过很多次"
    st["narrative_scope"] = "self"
    block = companion.format_block(st)
    assert "我眼里的自己" in block and "对对方的整体印象" in block
    assert "对他" not in block


def test_answer_memory_block_carries_subject_tag():
    txt = answer.build_memory_block([
        {"kind": "verified", "gist": "我答应过每周都来", "subject": "self",
         "occurred_at": "", "created_at": "2026-11-15", "affect": {}},
        {"kind": "verified", "gist": "用户怕狗", "subject": "counterpart",
         "occurred_at": "", "created_at": "2026-11-15", "affect": {}},
    ])
    assert "[关于我]" in txt and "[关于对方]" in txt


def test_subject_survives_recall(memdb, conn, offline):
    from neuromem import recall as _recall
    _event(conn, "evt_r_1", "我答应过每周都来", "self")
    eid = store.remember(conn, "我答应过每周都来", speaker="用户",
                         subject="self")[0]
    items = _recall.recall(conn, "我答应过什么")
    assert any(it.get("subject") == "self" for it in items), \
        "subject 必须跟着候选一路到生成层，否则 MCP 标注永远是默认值"


# ---------------- 阶段 2：多维情感 ----------------
def test_affect_parse_multiple_shapes():
    assert affect.parse_emotions({"joy": 0.7})["joy"] == 0.7
    assert "sadness" in affect.parse_emotions("有点难过")
    assert affect.parse_emotions("开心")["joy"] > 0
    assert affect.parse_emotions({"valence": -0.8, "arousal": 0.9})
    assert affect.parse_emotions(None) == {}


def test_affect_decay_by_real_time(memdb, conn):
    affect.update(conn, {"fear": 0.9}, person="对方")
    s0 = affect.snapshot(conn)
    assert s0["top"] == "fear" and s0["intensity"] > 0.8
    mood0, vec0 = s0["mood"]["fear"], s0["vector"]["fear"]
    old = (datetime.now(timezone.utc).astimezone()
           - timedelta(hours=config.AFFECT_HALFLIFE_HOURS)).isoformat(
               timespec="seconds")
    conn.execute("UPDATE affect_state SET updated_at=? WHERE id=1", (old,))
    conn.commit()
    s1 = affect.snapshot(conn)
    assert 0.4 < s1["intensity"] < 0.55, "6 小时半衰期应约等于一半"
    # 慢心境是低通滤波：绝对值比当下情绪小，但衰减比例更慢（72h vs 6h）
    assert (s1["mood"]["fear"] / mood0) > (s1["vector"]["fear"] / vec0), \
        "慢心境应比当下情绪退得慢"


def test_affect_expression_and_initiation(memdb, conn):
    affect.update(conn, {"sadness": 0.9}, person="对方")
    snap = affect.snapshot(conn)
    hints = affect.expression_hints(snap)
    assert any("语气放轻" in h for h in hints)
    assert any("短句" in h for h in hints) or snap["energy"] >= 0.35
    assert affect.should_initiate(snap, idle_hours=0.5, has_open_topic=True)[0] is False
    assert affect.should_initiate(snap, idle_hours=10, has_open_topic=False)[0] is False
    assert affect.should_initiate(snap, idle_hours=10, has_open_topic=True)[0] is False, \
        "低能量不该主动打扰"


def test_affect_syncs_legacy_emotion_state(memdb, conn):
    affect.update(conn, {"joy": 0.9}, person="对方")
    row = conn.execute("SELECT valence,arousal FROM emotion_state WHERE id=1").fetchone()
    assert row["valence"] > 0, "离散情绪要折回旧两维，让既有召回偏置继续生效"


def test_affect_relation_and_event_log(memdb, conn):
    affect.update(conn, {"joy": 0.7}, person="小明", trigger="单测")
    affect.update(conn, {"anger": 0.5}, person="小明", trigger="单测")
    rel = affect.relation(conn, "小明")
    assert rel["intimacy"] > 0 and rel["label"]
    assert conn.execute("SELECT COUNT(*) FROM affect_events").fetchone()[0] == 2
    assert "小明" in affect.snapshot(conn)["relations"]


def test_brain_remember_boosts_salience(memdb, offline):
    b = Brain(session_id="boost")
    plain = b.remember("我记下了一件事", subject="self")[0]
    strong = b.remember("我记下了另一件事", subject="self",
                         affect_input={"surprise": 0.9})[0]
    s_plain = b.conn.execute("SELECT salience FROM events WHERE id=?",
                              (plain,)).fetchone()[0]
    s_strong = b.conn.execute("SELECT salience FROM events WHERE id=?",
                               (strong,)).fetchone()[0]
    assert s_strong > s_plain, "强情绪应提高编码显著性"
    b.close()


# ---------------- 阶段 2/3：Brain + Cortex ----------------
def test_brain_turn_pack(memdb, conn, offline):
    identity.set_profile(conn, {"who": "小伴"}, actor="human")
    b = Brain(session_id="pack")
    b.remember("我答应过每天都在这里", subject="self")
    pack = b.turn("我答应过什么")
    assert isinstance(pack, ContextPack)
    assert "小伴" in pack["identity_block"]
    assert pack["policy"] and "used_ids" in pack["policy"]
    assert pack.candidate_ids() == [c["id"] for c in pack["candidates"]]
    assert "固定身份" in pack.render()
    assert any("关于我自己" in h for h in pack["hints"])
    b.close()


def test_brain_commit_writes_all_layers(memdb, conn, offline):
    b = Brain(session_id="commit")
    eid = b.remember("我答应过每周都来", subject="self")[0]
    pack = b.turn("我答应过什么")
    out = b.commit("我记得我说过每周都来。", used_ids=[eid],
                   query_text=pack["query"], context_text="我说过每周都来",
                   affect_input={"joy": 0.7}, user_text="我答应过什么",
                   topic="约定")
    assert out["log_id"] and out["turn_id"] and out["context_text_ok"]
    assert conn.execute(
        "SELECT COUNT(*) FROM adoption_log WHERE source='agent'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM conversation_turns").fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM affect_events").fetchone()[0] >= 1
    # 反思插件只在「没依据可用」这类异常轮写日志，正常轮不制造噪声
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name='reflection_log'").fetchone()
    b.commit("这轮没有可用记忆", used_ids=[], query_text="随便问一句",
             context_text="", outcome="refused")
    assert conn.execute(
        "SELECT COUNT(*) FROM reflection_log").fetchone()[0] >= 1
    assert b.conn.execute(
        "SELECT activation FROM events WHERE id=?", (eid,)).fetchone()[0] is not None
    b.close()


def test_brain_cannot_let_agent_rewrite_l0(memdb, conn):
    b = Brain()
    assert not hasattr(b, "set_identity"), \
        "Brain 是 agent 接口，不能暴露任何 L0 写入口（否则可伪造 actor='human'）"
    assert b.identity_block() and "固定身份" in b.identity_block()
    denied = identity.set_profile(conn, {"who": "我是另一个人"}, actor="llm")
    assert denied["ok"] is False
    assert "小伴" not in (identity.get(conn) or {}).get("profile", {}).get("who", "")
    b.close()


def test_cortex_modules_and_fault_isolation(memdb, conn, offline):
    assert "proactive" in cortex.available() and "reflection" in cortex.available()

    class Broken(cortex.BrainModule):
        name = "broken"

        def contribute(self, pack):
            raise RuntimeError("插件故意炸")

    cortex.REGISTRY["broken"] = Broken
    try:
        b = Brain(session_id="fault", modules=["broken", "reflection"])
        pack = b.turn("你好")
        assert isinstance(pack["module_notes"], dict), "坏插件不能拖垮 turn"
        b.commit("你好呀", used_ids=[], query_text="你好",
                 context_text="", outcome="refused")
        assert b.tick().get("reflection")
        b.close()
    finally:
        cortex.REGISTRY.pop("broken", None)


def test_cortex_can_be_disabled(memdb, conn, offline, monkeypatch):
    monkeypatch.setattr(config, "CORTEX_ENABLED", False)
    b = Brain(session_id="off")
    assert b.cortex.active == []
    assert b.turn("你好")["module_notes"] == {}
    b.close()


def test_reset_covers_v13_tables_but_keeps_l0(memdb, conn, offline):
    from neuromem import api
    identity.get(conn)          # 固定身份行是惰性写入，先确保它存在
    affect.update(conn, {"joy": 0.6}, person="对方")
    b = Brain()
    b.commit("你好", used_ids=[], query_text="你好", context_text="",
            outcome="refused")
    b.close()
    r = api.reset_all({"confirm": "RESET"})
    assert r["ok"], r.get("error")
    for t in ("affect_state", "affect_events", "affect_relations",
              "reflection_log", "adoption_log", "conversation_turns"):
        assert t in r["cleared"], "清库动态枚举必须覆盖新表：" + t
        assert conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM identity").fetchone()[0] == 1
