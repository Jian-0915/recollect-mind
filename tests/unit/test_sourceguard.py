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
