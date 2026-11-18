"""意图层测试：规则触发、私人词排除、模型失败降级。"""
from neuromem import intent


def test_extract_place_entity():
    got = intent.extract_entities("今天我们聊了乌江寨，那里有吊脚楼")
    assert "乌江寨" in got


def test_extract_explain_entity():
    got = intent.extract_entities("你说的那个‘尊嘟假嘟’是什么梗")
    assert "尊嘟假嘟" in got


def test_extract_quoted_entity():
    got = intent.extract_entities("他提到《长安的荔枝》这本书")
    assert "长安的荔枝" in got


def test_exclude_private_terms():
    got = intent.extract_entities("微微说她去了乌江寨", private_terms=["微微"])
    assert "乌江寨" in got
    assert "微微说她去了乌江寨" not in got


def test_exclude_stopwords():
    got = intent.extract_entities("我们今天聊了些什么呢")
    assert got == []


def test_build_intent_unknown():
    i = intent.build_intent("乌江寨是什么地方", private_terms=[])
    assert i.need_search and i.entity == "乌江寨"
    assert "乌江寨" in i.query


def test_build_intent_known_skips_search():
    i = intent.build_intent("乌江寨是什么地方", known=True)
    assert not i.need_search and "已有" in i.reason


def test_build_intent_no_entity():
    i = intent.build_intent("今天心情不错")
    assert not i.need_search


def test_refine_intent_uses_llm(monkeypatch):
    i = intent.build_intent("乌江寨是什么地方")
    def fake(prompt, **kw):
        return {"need_search": True, "query": "乌江寨 旅游", "reason": "x"}
    out = intent.refine_intent(i, "乌江寨是什么地方", llm=fake)
    assert out.query == "乌江寨 旅游"


def test_refine_intent_degrades_on_bad_json():
    i = intent.build_intent("乌江寨是什么地方")
    original = i.query
    def bad(prompt, **kw):
        return {"need_search": True, "query": "微微的私密日记"}
    out = intent.refine_intent(i, "乌江寨是什么地方", llm=bad)
    assert out.query == original


def test_refine_intent_degrades_on_exception():
    i = intent.build_intent("乌江寨是什么地方")
    original = i.query
    def boom(prompt, **kw):
        raise RuntimeError("llm down")
    out = intent.refine_intent(i, "乌江寨是什么地方", llm=boom)
    assert out.query == original
