"""P0 回归：清库冷启动下的人物硬过滤误伤（2026-11-15 主库 18 轮实测定位）。

复现路径：personas 自动登记「我 / 你」等代词 → 长度 1 的名字在几乎每个
问句里命中 → pids 非空 → 末尾硬过滤只保留有 involves 边的事件，而清库后
新写入的事实还没建边，于是全部候选被丢弃，余弦最高的正确答案也进不去。

本文件的嵌入全部用固定小向量 mock，不依赖 Ollama。
"""
import json
from datetime import datetime, timezone

import pytest

from agent.report import _trace_metrics
from neuromem import config, db, embed, recall

DIM = 8


def _vec(i):
    v = [0.0] * DIM
    v[i % DIM] = 1.0
    return v


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _event(conn, eid, gist, vec=0, subject="counterpart", place=""):
    conn.execute(
        "INSERT INTO events(id,created_at,gist,place,embedding,affect_json,"
        "salience,activation,surprise,confidence,archived,recall_count,subject) "
        "VALUES(?,?,?,?,?,?,?,1.0,0.5,0.9,0,0,?)",
        (eid, _now(), gist, place, db.pack(_vec(vec)), json.dumps({}), 0.5,
         subject))


def _persona(conn, pid, name, aliases=None):
    conn.execute("INSERT INTO personas(id,canonical_name,aliases_json) VALUES(?,?,?)",
                 (pid, name, json.dumps(aliases or [], ensure_ascii=False)))


@pytest.fixture()
def cold(conn, monkeypatch):
    """一条正确事实 + 两条无关事实，personas 里混进代词，全库零 edges。"""
    monkeypatch.setattr(embed, "embed", lambda text: _vec(0))
    _event(conn, "e_answer", "你叫小林，在杭州做平面设计", vec=0)
    _event(conn, "e_other1", "你上次答应我每周都来陪我聊天", vec=1)
    _event(conn, "e_other2", "我们说好每个月一起看一次电影", vec=2)
    _persona(conn, "p_me", "我")
    _persona(conn, "p_you", "你")
    _persona(conn, "p_lin", "小林")
    conn.commit()
    return conn


def test_pronoun_persona_is_not_a_person_mention(cold, monkeypatch):
    monkeypatch.setattr(config, "RECALL_COLDSTART_FIX", True)
    assert recall._query_personas(cold, "我叫什么？在哪儿工作？") == []
    # 真人名字仍然照常命中
    assert recall._query_personas(cold, "小林最近怎么样") == ["p_lin"]

    monkeypatch.setattr(config, "RECALL_COLDSTART_FIX", False)
    assert "p_me" in recall._query_personas(cold, "我叫什么？在哪儿工作？")


def test_noise_2gram_is_dropped(monkeypatch):
    monkeypatch.setattr(config, "RECALL_COLDSTART_FIX", True)
    assert "儿工" not in recall._keywords("我叫什么？在哪儿工作？")
    assert "工作" in recall._keywords("我叫什么？在哪儿工作？")

    monkeypatch.setattr(config, "RECALL_COLDSTART_FIX", False)
    assert "儿工" in recall._keywords("我叫什么？在哪儿工作？")


def test_cold_start_recall_fix_on_vs_off(cold, monkeypatch):
    q = "我叫什么？在哪儿工作？"
    monkeypatch.setattr(config, "RECALL_COLDSTART_FIX", False)
    old = recall.recall(cold, q, top_k=5)
    assert old == [], "修复前应复现「pids 非空 + 零 edges → 全部候选被丢弃」"

    monkeypatch.setattr(config, "RECALL_COLDSTART_FIX", True)
    fixed = recall.recall(cold, q, top_k=5)
    assert [x["id"] for x in fixed][0] == "e_answer"
    assert fixed[0]["score"] > 0.30


def test_non_pronoun_person_without_edges_also_not_wipe_all(cold, monkeypatch):
    """第二层保护：即使点名的是真人，只要 ent_ids 为空也不能清空候选。"""
    monkeypatch.setattr(config, "RECALL_COLDSTART_FIX", True)
    fixed = recall.recall(cold, "小林在哪儿工作", top_k=5)
    assert [x["id"] for x in fixed][0] == "e_answer"

    monkeypatch.setattr(config, "RECALL_COLDSTART_FIX", False)
    assert recall.recall(cold, "小林在哪儿工作", top_k=5) == []


def test_person_filter_still_works_when_edges_exist(cold, monkeypatch):
    """有边时人物过滤仍然生效，修复不能退化为「永远不过滤」。"""
    cold.execute("INSERT INTO edges(source,target,type,weight) VALUES(?,?,?,?)",
                 ("e_other1", "p_lin", "involves", 1.0))
    # 让两条都有足够的基础分，才看得出「人物闸门」而不是「绝对分门槛」
    cold.execute("UPDATE events SET embedding=? WHERE id='e_other1'",
                 (db.pack(_vec(0)),))
    cold.commit()
    monkeypatch.setattr(config, "RECALL_COLDSTART_FIX", True)
    items = recall.recall(cold, "小林在哪儿工作", top_k=8)
    ids = [x["id"] for x in items]
    assert "e_other1" in ids
    # 只有与该人物连边的事件能过人物闸门（正确答案本轮没建边，不该混入）
    assert "e_answer" not in ids
    assert "e_other2" not in ids


def test_focus_noise_no_longer_penalizes_everything(cold, monkeypatch):
    """焦点词全库无命中（分词噪声）时不该对每条候选乘 0.55。

    先清空 personas，隔离掉人物硬过滤，只比较 focus / 分词清洗的效果。
    """
    cold.execute("DELETE FROM personas")
    cold.commit()
    q = "你到底在哪儿工作来着"      # 2-gram 会切出「儿工」等噪声，全库无命中
    monkeypatch.setattr(config, "RECALL_COLDSTART_FIX", False)
    old = recall.recall(cold, q, top_k=5)
    monkeypatch.setattr(config, "RECALL_COLDSTART_FIX", True)
    new = recall.recall(cold, q, top_k=5)
    assert old and new
    assert new[0]["score"] > old[0]["score"]


def _row(used, ctx_ok, outcome="answered", reply="嗯"):
    return {"reply": reply, "used_ids": used, "context_ok": ctx_ok,
            "outcome": outcome, "parse_ok": True, "llm_error": False,
            "candidate_count": 1, "invalid_used_ids": [], "remembered_ids": [],
            "fact_errors": [], "elapsed_ms": 1, "mood_top": "-"}


def test_report_context_rate_denominator_matches():
    trace = [_row(["e1"], True), _row([], True), _row([], True), _row(["e2"], False)]
    m = _trace_metrics(trace)
    assert m["turns_with_used_memory"] == 2
    assert m["context_text_rate_when_used"] == 0.5   # 修复前会是 1.5
    assert 0 <= m["context_text_rate_when_used"] <= 1
