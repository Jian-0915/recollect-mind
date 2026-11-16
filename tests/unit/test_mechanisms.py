"""v11 机制回归：再巩固版本链 / 情绪时间衰减 / 两条调节通路。

所有嵌入都用固定小向量 mock，不依赖 Ollama。
"""
import json
from datetime import datetime, timedelta, timezone

from neuromem import (adoption, companion, config, db, embed, emotion,
                      recall, reconsolidation)

DIM = 8


def _now():
    return datetime.now(timezone.utc).astimezone()


def _vec(i):
    v = [0.0] * DIM
    v[i % DIM] = 1.0
    return v


def _event(conn, eid, gist="团子的事", affect=None, vec=0, salience=0.3):
    conn.execute(
        "INSERT INTO events(id,created_at,gist,embedding,affect_json,salience,"
        "activation,surprise,confidence,archived,recall_count) "
        "VALUES(?,?,?,?,?,?,1.0,0.5,0.9,0,0)",
        (eid, _now().isoformat(timespec="seconds"), gist, db.pack(_vec(vec)),
         json.dumps(affect or {}, ensure_ascii=False), salience))


def _set_emotion(conn, valence, arousal=0.5, hours_ago=0):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS emotion_state("
        "id INTEGER PRIMARY KEY CHECK(id=1), valence REAL DEFAULT 0, "
        "arousal REAL DEFAULT 0, label TEXT, updated_at TEXT)")
    ts = (_now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR REPLACE INTO emotion_state(id,valence,arousal,label,updated_at) "
        "VALUES(1,?,?,?,?)", (valence, arousal, "低落" if valence < 0 else "愉快", ts))


def test_reconsolidation_keeps_versions_and_norm(memdb, conn, monkeypatch):
    monkeypatch.setattr(embed, "embed", lambda text: _vec(2))
    _event(conn, "e1", gist="团子怕吹风机", vec=0)
    conn.commit()
    before = db.unpack(conn.execute(
        "SELECT embedding FROM events WHERE id='e1'").fetchone()[0])
    rc = reconsolidation.reconsolidate(
        conn, ["e1"], context_text="团子害怕吹风机的声音")
    assert rc["changed"] == 1, rc
    row = conn.execute(
        "SELECT embedding,revision_count,consolidation_stage,search_text "
        "FROM events WHERE id='e1'").fetchone()
    after = db.unpack(row["embedding"])
    assert row["revision_count"] == 1
    assert row["consolidation_stage"] == "reconsolidated"
    # F3（2026-11-15）：search_text 只收用户原话（query），不收 agent 复述
    # （context_text），否则「采纳 → 更易召回 → 再采纳」会形成过热闭环。
    # 这里只传了 context_text，所以关键词索引必须保持为空；向量仍用复述重编码。
    assert not (row["search_text"] or ""), "复述不应成为永久检索入口"
    assert after != before, "再巩固应改变记忆表示"
    n0 = sum(x * x for x in before) ** 0.5
    n1 = sum(x * x for x in after) ** 0.5
    assert abs(n0 - n1) < 1e-4, "重编码不应改变向量模长"
    assert len(reconsolidation.versions(conn, "e1")) == 1
    assert reconsolidation.revert(conn, "e1")
    restored = db.unpack(conn.execute(
        "SELECT embedding FROM events WHERE id='e1'").fetchone()[0])
    assert restored == before, "回滚应恢复旧向量"


def test_reconsolidation_indexes_only_user_query(memdb, conn, monkeypatch):
    """F3 正向契约：用户原话可进关键词索引，agent 复述只用于向量重编码。"""
    monkeypatch.setattr(embed, "embed", lambda text: _vec(2))
    _event(conn, "e2", gist="团子怕吹风机", vec=0)
    conn.commit()
    rc = reconsolidation.reconsolidate(
        conn, ["e2"], query="团子怕吹风机吗",
        context_text="它一听见就躲到沙发底下")
    assert rc["changed"] == 1, rc
    row = conn.execute("SELECT search_text FROM events WHERE id='e2'").fetchone()
    assert "吹风机" in (row["search_text"] or "")
    assert "沙发" not in (row["search_text"] or ""), "复述不得成为检索入口"


def test_reconsolidation_is_throttled(memdb, conn, monkeypatch):
    monkeypatch.setattr(embed, "embed", lambda text: _vec(2))
    _event(conn, "e1", vec=0)
    conn.commit()
    reconsolidation.reconsolidate(conn, ["e1"], context_text="团子怕吹风机")
    rc = reconsolidation.reconsolidate(conn, ["e1"], context_text="又提了一次吹风机")
    assert rc["changed"] == 0 and rc["throttled"] == 1


def test_l1_signal_does_not_reconsolidate(memdb, conn, monkeypatch):
    monkeypatch.setattr(embed, "embed", lambda text: _vec(2))
    _event(conn, "e1", vec=0)
    conn.commit()
    adoption.auto_record(conn, query="团子怕什么", items=[{"id": "e1"}],
                         summary="团子怕吹风机", trace={"adopted_ids": ["e1"]})
    assert conn.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0] == 0


def test_emotion_decay_uses_real_time(memdb, conn):
    emotion.EmotionState(conn)
    _set_emotion(conn, -0.8, 0.8, hours_ago=12)   # 半衰期 6h → 两个半衰期
    out = emotion.EmotionState(conn).decay()
    assert abs(out["valence"] + 0.2) < 0.02, out


def test_remember_turn_updates_emotion(memdb, conn):
    emotion.EmotionState(conn)
    companion.remember_turn(conn, "用户", "今天有点累",
                            affect={"valence": -0.6, "arousal": 0.5})
    v = conn.execute("SELECT valence FROM emotion_state WHERE id=1").fetchone()[0]
    assert v < -0.1, "对话情绪应推动全局心境，实际 %s" % v


def test_emotion_bias_shifts_ranking(memdb, conn, monkeypatch):
    monkeypatch.setattr(embed, "embed", lambda text: _vec(0))
    # 先插正性、后插负性：无偏置时按插入顺序正性在前
    _event(conn, "positive", affect={"valence": 0.9, "arousal": 0.5}, vec=0)
    _event(conn, "negative", affect={"valence": -0.9, "arousal": 0.5}, vec=0)
    _set_emotion(conn, -0.8)
    conn.commit()
    monkeypatch.setattr(config, "EMOTION_BIAS", False)
    plain = recall.recall(conn, "团子", top_k=2)
    monkeypatch.setattr(config, "EMOTION_BIAS", True)
    biased = recall.recall(conn, "团子", top_k=2)
    assert plain and biased
    assert plain[0]["id"] == "positive", plain
    assert biased[0]["id"] == "negative", biased


def test_schema_bias_keeps_concepts_out_of_evidence(memdb, conn, monkeypatch):
    monkeypatch.setattr(embed, "embed", lambda text: _vec(0))
    _event(conn, "e1", vec=0)
    old = (_now() - timedelta(days=30)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO concepts(id,text,abstraction_level,confidence,created_at,embedding) "
        "VALUES('c1','团子害怕的声音',1,0.7,?,?)", (old, db.pack(_vec(0))))
    conn.execute(
        "INSERT INTO concept_links(concept_id,event_id,type) VALUES('c1','e1','derived_from')")
    conn.commit()
    monkeypatch.setattr(config, "SCHEMA_AS_EVIDENCE", False)
    items = recall.recall(conn, "团子", top_k=5)
    assert items and all(i["type"] != "concept" for i in items), items
    assert any(i.get("schema") for i in items), "簇内事件应获得图式偏置"
    monkeypatch.setattr(config, "SCHEMA_AS_EVIDENCE", True)
    items2 = recall.recall(conn, "团子", top_k=5)
    assert any(i["type"] == "concept" for i in items2), items2


def test_generic_question_answered_by_concepts(memdb, conn, monkeypatch):
    """「是个怎样的人」没有单一事件能答，应由语义图式给出理解。"""
    monkeypatch.setattr(embed, "embed", lambda text: _vec(0))
    _event(conn, "e1", vec=0)
    old = (_now() - timedelta(days=30)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO concepts(id,text,abstraction_level,confidence,created_at,embedding) "
        "VALUES('c1','用户是个念旧的人',1,0.7,?,?)", (old, db.pack(_vec(0))))
    conn.execute(
        "INSERT INTO concept_links(concept_id,event_id,type) "
        "VALUES('c1','e1','derived_from')")
    conn.commit()
    monkeypatch.setattr(config, "SCHEMA_AS_EVIDENCE", False)
    items = recall.recall(conn, "用户是个怎样的人", top_k=5)
    assert any(i["type"] == "concept" for i in items), items
    concrete = recall.recall(conn, "团子怕什么", top_k=5)
    assert all(i["type"] != "concept" for i in concrete), concrete
