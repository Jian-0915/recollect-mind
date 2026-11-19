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


def test_extract_strips_spoken_prefix_before_place_suffix():
    """2026-11-19 回归：口语前缀不能被地名后缀路径吞进实体。

    真实对话「你知道世界第一高桥在哪里吗」曾被抽成「你知道世界第一高桥」，
    「上周去了青岛」被抽成整句，拿去查必然 empty，F5 白跑。
    """
    assert intent.extract_entities("你知道世界第一高桥在哪里吗") == ["世界第一高桥"]
    assert intent.extract_entities("世界第一高桥在哪里") == ["世界第一高桥"]
    assert intent.extract_entities("上周去了青岛") == ["青岛"]
    # 回归：原本就该对的别弄坏
    assert intent.extract_entities("今天去了乌江寨玩") == ["乌江寨"]
    assert intent.extract_entities("乌江寨是什么地方") == ["乌江寨"]


def test_extract_info_query_question_patterns():
    """v44：求推荐/求介绍也要能查，不能只认带地名后缀的专名。"""
    assert intent.extract_info_query(
        "我在想贵州有什么景点，我想要去游玩一下") == "贵州景点"
    assert intent.extract_info_query("那除了黄果树瀑布呢，你还有什么推荐") == "黄果树瀑布推荐"
    assert intent.extract_info_query("我想了解一下青岛攻略") == "青岛攻略"


def test_extract_info_query_skips_statements_and_private_terms():
    """陈述句不查；称呼绝不进查询词。"""
    assert intent.extract_info_query("今天我去了乌江寨，微微说挺好玩的") == ""
    q = intent.extract_info_query("微微你知道贵州有什么景点吗", private_terms=["微微"])
    assert "微微" not in q and "贵州" in q
