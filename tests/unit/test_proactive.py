"""主动发起出口单测（v15）：候选→话语→消费，不碰 /api/ask，不编造。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from neuromem import companion, db, proactive


def _db(tmp_path):
    c = db.connect(str(tmp_path / "p.db"))
    # 造出满足 should_initiate 的状态：能量够、距上次对话够久、有未闭合话题
    companion.remember_turn(c, "对方", "上次聊到章鱼小丸子偏甜口还是咸口")
    companion.update_state(c, open_topics=["章鱼小丸子偏甜口还是咸口", "表哥加班狠到什么程度"])
    # last_turn_at 不在 update_state 的 allowed 列表里，直接改会话表
    c.execute("UPDATE companion_state SET last_turn_at=? WHERE id=1",
              ("2020-01-01T00:00:00+08:00",))
    c.commit()
    from neuromem import affect
    affect.update(c, {"期待": 0.9}, person="对方", trigger="test")
    return c


def test_fabrication_detector():
    assert proactive._fabrication("上次那个 IPA 你常喝哪款？我最近想换换口味。")
    assert proactive._fabrication("我还没泡过温泉呢，你去的是哪？")
    assert not proactive._fabrication("我想起你上次说的表哥，他加班还那么狠吗？")
    assert not proactive._fabrication("上次那个章鱼小丸子，你后来偏甜还是偏咸？")


def test_peek_empty_when_no_candidate(tmp_path):
    c = _db(tmp_path)
    proactive._ensure(c)
    c.execute("DELETE FROM proactive_suggestions")
    c.commit()
    r = proactive.peek(c, llm_fn=lambda p, **k: "不该被调用")
    assert r["available"] is False
    c.close()


def test_tick_peek_consume_and_retire(tmp_path):
    c = _db(tmp_path)
    proactive._ensure(c)
    c.execute("DELETE FROM proactive_suggestions")
    c.commit()

    r = proactive.tick(c)
    assert r["proactive"]["created"] == 1

    # 防堆积：已有未消费候选就不再产出
    assert proactive.tick(c)["proactive"]["created"] == 0

    fixed = "上次那个章鱼小丸子，你后来是偏甜口还是咸口来着？"
    got = proactive.peek(c, llm_fn=lambda p, **k: fixed)
    assert got["available"] is True
    assert got["text"] == fixed
    assert got["generated"] is True

    # 展示即消费：同一条不会被取两次
    assert proactive.peek(c, llm_fn=lambda p, **k: fixed)["available"] is False

    # 提起过的话题（got["topic"]）从 open_topics 退场，避免下次又提同一件
    topics = companion.state(c).get("open_topics") or []
    assert not any(got["topic"] in t for t in topics)
    # 另一个没提的话题还在
    other = "章鱼" if "表哥" in got["topic"] else "表哥"
    assert any(other in t for t in topics)
    c.close()


def test_fabrication_falls_back_to_plain(tmp_path):
    c = _db(tmp_path)
    proactive._ensure(c)
    c.execute("DELETE FROM proactive_suggestions")
    c.commit()
    proactive.tick(c)
    got = proactive.peek(c, llm_fn=lambda p, **k: "上次那个 IPA，我最近也想换口味。")
    assert got["generated"] is False
    assert "上次那个" in got["text"] and "我" not in got["text"]
    c.close()
