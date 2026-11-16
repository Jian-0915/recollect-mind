"""v14 生成层护栏回归：事实约束 / 解析重试 / 兜底提取（全部离线 fake LLM）。

对应清库后 18 轮 A/B 里暴露的 P1：采用真实候选「家里养了只猫叫年糕」后，
回复凭空补出「小胖橘 / 贪吃 / 一开罐头就冲过来」，而 used_ids 合法、
context_text 也正确复述，原有白名单完全拦不住。
"""
import json

import pytest

from agent import companion as ag
from agent.companion import (CompanionAgent, candidate_evidence, claim_grounded,
                            fallback_fact, normalize_claims, parse_output,
                            trim_reply, validate_claims)
from agent.report import _trace_metrics
from neuromem import config, db, store
from neuromem.brain import ContextPack


@pytest.fixture()
def offline(monkeypatch):
    dim = config.EMBED_DIM
    monkeypatch.setattr(store.embed, "embed",
                        lambda t, *a, **k: [1.0] + [0.0] * (dim - 1))
    monkeypatch.setattr(store.persona, "extract_relations", lambda *a, **k: [])
    monkeypatch.setattr(store.llm, "generate", lambda *a, **k: "（离线自述）")
    monkeypatch.setattr(config, "DUP_SIM", 2.0)
    monkeypatch.setattr(config, "SUPERSEDE_SIM", 2.0)
    monkeypatch.setattr(ag, "FACT_GUARD", True)
    monkeypatch.setattr(ag, "PARSE_RETRY", True)
    monkeypatch.setattr(ag, "REMEMBER_FALLBACK", True)
    return monkeypatch


def _seq(*raws):
    it = iter(raws)
    return lambda prompt: next(it)


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


def test_parse_output_tolerates_noise():
    assert parse_output('```json\n{"reply":"好呀","used_ids":[],}\n```')[1]
    assert parse_output('前缀 {"reply":"好呀"} 后面还有个 } 杂字')[0]["reply"] == "好呀"
    assert parse_output('{"reply":"好呀",,}')[0]["reply"] == "好呀"
    d, ok = parse_output("抱歉，我不会输出 JSON")
    assert not ok and d["outcome"] == "refused"


def test_claims_whitelist_and_grounding():
    ev = ["家里养了只猫叫年糕", "算了不提了，跟我说说我的猫吧"]
    assert claim_grounded("家里养了只猫叫年糕", ev)
    assert not claim_grounded("那只小胖橘很贪吃", ev)

    claims = normalize_claims([
        {"text": "家里养了只猫叫年糕", "source_id": "e1"},
        {"text": "那只小胖橘很贪吃", "source_id": "e1"},
        {"text": "西湖边溜达过", "source_id": "not_in_candidates"},
        "不是结构化的",
    ])
    assert len(claims) == 3
    ok, bad, missing = validate_claims(claims, ["e1"], ev)
    assert not ok and not missing
    assert [why for _, why in bad] == [
        "依据原文里找不到这句话", "source_id 不在本轮采用的候选里"]
    ok2, _, missing2 = validate_claims([], ["e1"], ev)
    assert not ok2 and missing2
    assert validate_claims([], [], ev)[0]       # 没用记忆就不该要求申报


def test_trim_reply_removes_only_violating_sentence():
    reply = ("年糕呀，那只小胖橘对吧？我记得你老说他贪吃，一开罐头就冲过来。"
             "今天他有没有趴你腿上陪你？")
    out = trim_reply(reply, [("那只小胖橘", "依据原文里找不到这句话")],
                     "家里养了只猫叫年糕")
    assert "小胖橘" not in out
    assert "今天他有没有" in out
    assert trim_reply("那只小胖橘。", [("那只小胖橘", "x")], "家里养了只猫叫年糕") \
        == "我记得有这么件事：家里养了只猫叫年糕"
    # 兜底话术不能再归因成「你说过」：context_text 现在是候选原文，
    # 里面可能是别人的事（「阿岚养了只狗叫布丁」），说成「你说过」就是硬错。


def test_fallback_fact_only_for_past_concrete_events():
    assert "牙医" in fallback_fact("上周三我去看了牙医，紧张得手心全是汗")
    assert fallback_fact("我去年国庆去了哪个国家玩？") == ""
    assert fallback_fact("算了不提了，跟我说说我的猫吧") == ""
    assert fallback_fact("[id:evt_x] 上周去了") == ""
    assert fallback_fact("嗯") == ""


def _pack(eid, gist):
    return ContextPack(query="q", candidates=[{
        "id": eid, "gist": gist, "fragments": [gist],
        "subject": "counterpart", "subject_label": "对方", "score": 0.8,
    }])


def _agent(memdb, offline, llm_fn):
    a = CompanionAgent(db_path=memdb, llm_fn=llm_fn, trace_path=None)
    return a


def test_parse_retry_recovers(memdb, conn, offline):
    good = _json({"reply": "好呀，我在这", "used_ids": [], "remember": [],
                  "affect": {}, "outcome": "answered"})
    a = _agent(memdb, offline, _seq("嗯……我不太会输出 JSON", good))
    try:
        row = a.step("今天好累啊")
        assert row["parse_ok"] and row["parse_retry_ok"] and row["parse_retried"]
        assert row["reply"] == "好呀，我在这"
    finally:
        a.close()


def test_fact_guard_rewrites_fabrication(memdb, conn, offline):
    eid = store.remember(conn, "家里养了只猫叫年糕", speaker="用户")[0]
    conn.commit()
    bad_reply = ("年糕呀，那只小胖橘对吧？我记得你老说他贪吃，一开罐头就冲过来。"
                 "今天他有没有趴你腿上陪你？")
    first = _json({
        "reply": bad_reply, "used_ids": [eid],
        "context_text": "家里养了只猫叫年糕",
        "claims": [
            {"text": "家里养了只猫叫年糕", "source_id": eid},
            {"text": "那只小胖橘很贪吃", "source_id": eid},
        ],
        "remember": [], "affect": {}, "outcome": "answered"})
    fixed = _json({
        "reply": "年糕呀，我记得你家里养了只猫叫年糕。",
        "claims": [{"text": "家里养了只猫叫年糕", "source_id": eid}]})
    a = _agent(memdb, offline, _seq(first, fixed))
    try:
        a.brain.turn = lambda text, **kw: _pack(eid, "家里养了只猫叫年糕")
        row = a.step("跟我说说我的猫吧")
        assert row["fact_guard"] and row["fact_violation"]
        assert row["fact_rewritten"] and not row["fact_trimmed"]
        assert "小胖橘" not in row["reply"] and "贪吃" not in row["reply"]
        assert row["used_ids"] == [eid], "收敛不能推翻本轮采纳判断"
        assert candidate_evidence(_pack(eid, "家里养了只猫叫年糕"),
                                  [eid], "跟我说说我的猫吧", [])
    finally:
        a.close()


def test_fact_guard_off_keeps_old_behavior(memdb, conn, offline, monkeypatch):
    monkeypatch.setattr(ag, "FACT_GUARD", False)
    eid = store.remember(conn, "家里养了只猫叫年糕", speaker="用户")[0]
    conn.commit()
    out = _json({"reply": "年糕呀，那只小胖橘对吧？", "used_ids": [eid],
                 "context_text": "家里养了只猫叫年糕", "remember": [],
                 "affect": {}, "outcome": "answered"})
    a = _agent(memdb, offline, _seq(out))
    try:
        a.brain.turn = lambda text, **kw: _pack(eid, "家里养了只猫叫年糕")
        row = a.step("跟我说说我的猫吧")
        assert not row["fact_guard"] and not row["fact_violation"]
        assert "小胖橘" in row["reply"]
        assert row["parse_retried"] is False
    finally:
        a.close()


def test_remember_fallback_writes_fact(memdb, conn, offline):
    out = _json({"reply": "哎，看牙确实吓人", "used_ids": [], "remember": [],
                 "affect": {}, "outcome": "answered"})
    a = _agent(memdb, offline, _seq(out))
    try:
        row = a.step("上周三我去看了牙医，紧张得手心全是汗")
        assert row["remember_fallback"] is True
        assert any("牙医" in t for t in row["remember"])
        assert len(row["remembered_ids"]) >= 1
    finally:
        a.close()


def _row(**kw):
    base = {"reply": "嗯", "used_ids": [], "context_ok": False,
            "outcome": "answered", "parse_ok": True, "llm_error": "",
            "candidate_count": 1, "invalid_used_ids": [], "remembered_ids": [],
            "fact_errors": [], "elapsed_ms": 1, "mood_top": "-"}
    base.update(kw)
    return base


def test_report_guard_metrics():
    trace = [
        _row(fact_guard=True, fact_violation=True, fact_rewritten=True,
             parse_retried=True, parse_retry_ok=True, remember_fallback=True),
        _row(fact_guard=True, claims_missing=True),
        _row(),
    ]
    m = _trace_metrics(trace)
    assert m["fact_guard_turns"] == 2
    assert m["fact_violation_turns"] == 1
    assert m["fact_rewrite_turns"] == 1
    assert m["claims_missing_turns"] == 1
    assert m["grounded_reply_rate"] == 0.5
    assert m["parse_retry_success"] == 1
    assert m["remember_fallback_turns"] == 1


# ---------------- 2026-11-16 写入敏感度回归 ----------------
def test_fallback_covers_today_and_short_first_person():
    """旧版只认「上周/昨天/上次」，「今天」的小事被整轮丢掉 —— 这是实测 bug。"""
    assert fallback_fact("金毛今天撞我的腿，可爱死了") == "金毛今天撞我的腿，可爱死了"
    assert fallback_fact("我今天不太开心") == "我今天不太开心"
    assert fallback_fact("我学篆刻，刻了方闲章送自己。") == "我学篆刻，刻了方闲章送自己"
    # 追问 / 要求我记事，仍然不能写成事实
    assert fallback_fact("金毛今天干嘛了") == ""
    assert fallback_fact("它今天不是撞我腿了嘛") == ""
    assert fallback_fact("我今天做了什么") == ""


def test_claims_may_cite_user_text_as_source():
    """对方本轮刚说的话是合法依据：旧版只认候选 id，把刚说过的事实当编造裁掉。"""
    user = "2026年8月我去了趟大理，住了五天"
    claims = normalize_claims([
        {"text": "你八月去了大理，住了五天", "source_id": "user"},
        {"text": "你春天想去洱海边的民宿", "source_id": "user"},
    ])
    ok, bad, missing = validate_claims(claims, [], [user], user)
    assert not missing
    assert [t for t, _ in bad] == ["你春天想去洱海边的民宿"]
    # 不提供 user_text 时退回整段依据，不能因此放行编造
    assert not validate_claims(claims, [], ["家里养了只猫"], None)[0]


def test_trim_reply_falls_back_to_user_words():
    out = trim_reply("那只小胖橘很贪吃。", [("那只小胖橘", "x")], "",
                     "我家猫叫年糕")
    assert "年糕" in out and "小胖橘" not in out
