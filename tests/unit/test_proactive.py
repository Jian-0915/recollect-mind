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


def test_self_directive_leak_is_blocked():
    """用户截图里那条：模型把「该怎么说」当成了要发的话。"""
    bad = "我需要自然地提起这个话题，语气轻柔，带着一点淡淡的哀愁，像是随手一提。"
    assert proactive._unusable(bad)
    assert proactive._unusable("我会用带点委屈的口吻问他去哪了。")
    # 正常的话不能被误伤
    assert not proactive._unusable("上次那个麻辣烫，后来怎么样了？")
    assert not proactive._unusable("人呢，就这么没声了？")
    assert not proactive._unusable("我需要你陪我说会儿话。")


def test_self_directive_leak_is_blocked():
    """用户截图里那条：模型把「该怎么说」当成了要发的话。"""
    bad = "我需要自然地提起这个话题，语气轻柔，带着一点淡淡的哀愁，像是随手一提。"
    assert proactive._unusable(bad)
    assert proactive._unusable("我会用带点委屈的口吻问他去哪了。")
    # 正常的话不能被误伤
    assert not proactive._unusable("上次那个麻辣烫，后来怎么样了？")
    assert not proactive._unusable("人呢，就这么没声了？")
    assert not proactive._unusable("我需要你陪我说会儿话。")


def test_compose_falls_back_when_model_explains_itself(tmp_path):
    """整条出口链路：模型交代写法 → 重试仍交代 → 必须退回兜底，绝不弹给用户。"""
    c = _db(tmp_path)
    bad = "我需要自然地提起这个话题，语气轻柔，带着一点淡淡的哀愁，像是随手一提。"
    text, ok = proactive.compose(c, {"kind": "missing", "topic": "麻辣烫"},
                                  llm_fn=lambda p, **k: bad)
    assert ok is False
    assert "我需要" not in text and "语气" not in text
    assert "麻辣烫" in text
    c.close()


def test_compose_falls_back_when_model_explains_itself(tmp_path):
    """整条出口链路：模型交代写法 → 重试仍交代 → 必须退回兜底，绝不弹给用户。"""
    c = _db(tmp_path)
    bad = "我需要自然地提起这个话题，语气轻柔，带着一点淡淡的哀愁，像是随手一提。"
    text, ok = proactive.compose(c, {"kind": "missing", "topic": "麻辣烫"},
                                  llm_fn=lambda p, **k: bad)
    assert ok is False
    assert "我需要" not in text and "语气" not in text
    assert "麻辣烫" in text
    c.close()


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


def test_tick_peek_consume_and_cooldown(tmp_path):
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

    # v16：提过不再立刻删掉（那等于忘事），而是进冷却；提两次没回应才放下
    topics = companion.state(c).get("open_topics") or []
    assert any(got["topic"] in t for t in topics), "提过一次仍该记得这件事"
    row = c.execute("SELECT raised_at,raised_count FROM topic_state WHERE text=?",
                    (got["topic"],)).fetchone()
    assert row and row["raised_at"] and row["raised_count"] == 1

    # 冷却期内不会换个说法再问同一件事
    from neuromem import motivation
    again = motivation.pick(c)
    assert again["ok"] is False and ("冷却" in again["reason"] or "刚说过话" in again["reason"])
    c.close()


def test_fabrication_falls_back_to_plain(tmp_path):
    c = _db(tmp_path)
    proactive._ensure(c)
    c.execute("DELETE FROM proactive_suggestions")
    c.commit()
    proactive.tick(c)
    got = proactive.peek(c, llm_fn=lambda p, **k: "上次那个 IPA，我最近也想换口味。")
    assert got["generated"] is False
    # 兜底句有好几条（按心情随机取），但底线是：不冒充有经历，且确实提了那件事
    assert "我最近" not in got["text"] and not proactive._fabrication(got["text"])
    assert "章鱼小丸子" in got["text"], got["text"]
    c.close()
