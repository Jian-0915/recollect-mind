"""v44：外部事实来源纪律——不许把参数知识说成查来的。"""
from agent import prompts
from neuromem import sourceguard


BAD_REPLY = (
    "贵州好玩的地方可多啦～梵净山就很值得去，它是世界自然遗产，"
    "山顶的红云金顶超级壮观；马岭河峡谷号称地球上最美丽的伤疤，峡谷瀑布特别震撼～"
)


def test_unsourced_strong_claims_are_detected():
    bad = sourceguard.unsourced_parts(BAD_REPLY)
    assert len(bad) >= 2
    assert any("世界自然遗产" in x for x in bad)
    assert any("号称" in x for x in bad)


def test_subjective_opinion_is_not_a_claim():
    """「挺好玩/值得去」是评价，不是可核查的事实断言。"""
    assert sourceguard.unsourced_parts("那个地方挺好玩的，我觉得值得去～") == []


def test_knowledge_block_disables_hard_block():
    """资料已注入时由提示词约束，出口不再按字面误删引用了资料的话。"""
    assert sourceguard.unsourced_parts(
        BAD_REPLY, knowledge_block="【你自己查到的资料】梵净山是世界自然遗产") == []


def test_guard_reply_keeps_safe_parts_and_admits_no_search():
    out, changed = sourceguard.guard_reply(BAD_REPLY)
    assert changed is True
    assert "世界自然遗产" not in out and "号称" not in out
    assert "贵州好玩的地方可多啦" in out
    assert "没查过" in out


def test_guard_reply_leaves_ordinary_chat_untouched():
    s = "我在呢，想聊点什么？"
    assert sourceguard.guard_reply(s) == (s, False)


def test_guard_reply_all_bad_falls_back_to_honest_line():
    out, changed = sourceguard.guard_reply("梵净山是世界自然遗产，亚洲最大瀑布就在旁边。")
    assert changed and "没查过" in out and "梵净山" not in out


def test_asks_source():
    assert sourceguard.asks_source("你怎么知道这些景点的，你自己查的吗？")
    assert not sourceguard.asks_source("那我们明天去哪儿玩")


def test_prompt_states_three_sources_and_no_param_knowledge():
    assert "只认三处来源" in prompts.SYSTEM
    assert "没真查过" in prompts.SYSTEM
    assert "别拿自己记得的百科当查过的" in prompts.NO_BRAIN_SYSTEM
    p = prompts.build_prompt(None, "贵州有什么景点", [])
    assert "只认三处来源" in p


def test_knowledge_block_is_the_only_external_source():
    block = prompts.build_knowledge_block([
        type("H", (), {"title": "贵州景点", "snippet": "黄果树瀑布", "url": "https://x"})()
    ])
    assert "唯一可引用的外部事实来源" in block
    assert "不许拿你自己记得的百科补细节" in block


# --- v48 真实回归：资料只提茅台，却展开讲汾酒/泸州老窖/西凤酒 -------------
KAO_BLOCK = (
    "【你自己查到的资料】茅台酒是中国四大名酒之一（汾茅五剑第二），"
    "酱香型大曲白酒，产自贵州赤水河茅台镇。"
)
FOUR_WINES = (
    "汾酒是清香型的代表，口感清爽干净；泸州老窖是浓香型的老大哥，"
    "窖香浓郁、绵甜醇厚；西凤酒则是凤香型，香气独特、酒体醇厚，"
    "各有各的味道，真有意思～"
)


def test_extrapolated_wines_are_detected():
    """资料里只点了茅台，凭空报出的另三种酒必须被认出是资料外专名。"""
    names = sourceguard.unsourced_defined_subjects(FOUR_WINES, knowledge_block=KAO_BLOCK)
    assert {"汾酒", "泸州老窖", "西凤酒"}.issubset(set(names)), names


def test_whole_extrapolation_is_rejected():
    out, changed = sourceguard.guard_reply(FOUR_WINES, knowledge_block=KAO_BLOCK)
    assert changed and "没查过" in out
    assert "汾酒" not in out and "西凤酒" not in out


def test_correct_citation_not_treated_as_extrapolation():
    good = ("茅台酒呀，最出名的就是它的香气和口感啦～它是酱香型大曲白酒，"
            "原产于贵州赤水河的茅台镇，气味芳香醇厚浓郁，真有意思～")
    out, changed = sourceguard.guard_reply(good, knowledge_block=KAO_BLOCK)
    assert changed is False and out == good


def test_user_mentioned_terms_are_whitelisted():
    """对方自己聊过的词不算编造——白名单命中后不足阈值，不拦。"""
    out, changed = sourceguard.guard_reply(
        FOUR_WINES, knowledge_block=KAO_BLOCK,
        allowed_text="我们聊聊汾酒、泸州老窖和西凤酒")
    assert changed is False


def test_no_knowledge_block_keeps_old_behavior():
    """没资料时走 v44 强断言逻辑，本篇不含强断言→原样放行。"""
    assert sourceguard.guard_reply(FOUR_WINES) == (FOUR_WINES, False)
