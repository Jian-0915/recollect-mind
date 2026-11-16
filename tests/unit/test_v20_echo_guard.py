"""v20 回归：模型输出复述问题 / 念提示词时不能被当成台词。

背景：实测主库评测 27 题里有 6 题的回答是「对方的问题原样复述」或
「提示词里的格式要求」，根因是模型偶发不按 JSON 输出，而 parse_output
的纯文本兜底会把那段原文直接当 reply。这里守两道：解析后判一次，
重试后再判一次，仍不行就退回"没组织好"。
"""
import pytest

from agent.companion import looks_like_echo


@pytest.mark.parametrize("reply,user_text", [
    ("你现在住哪儿？", "你现在住哪儿？"),          # 完全相同
    ("你外婆身体怎么样了", "我外婆身体怎么样了？"),   # 去掉标点后仍包含问题主体
    ("我上一句说了什么？", "我上一句说了什么？"),
    ("你要说出口的话（自然口语，1~3 句，不要带任何标签）", "我表姐去哪了？"),
    ("请输出 JSON 格式", "我养了什么宠物？"),
])
def test_echo_detected(reply, user_text):
    assert looks_like_echo(reply, user_text)


@pytest.mark.parametrize("reply,user_text", [
    ("你上个月搬到城西了，离公司近但房租贵。", "你现在住哪儿？"),
    ("外婆今天出院了，你松了口气。", "我外婆身体怎么样了？"),
    ("你不喝牛奶，喝了会胀气。", "我喝牛奶吗？"),
    ("哎这个我记得——你每周三晚上去瑜伽，周末爱烘焙。", "我平时有哪些爱好？"),
    ("", "你现在住哪儿？"),
])
def test_normal_reply_not_echo(reply, user_text):
    assert not looks_like_echo(reply, user_text)


def test_echo_check_without_user_text():
    """拿不到问句时仍要挡住提示词片段（它会直接出现在回答里）。"""
    assert looks_like_echo("不要带任何标签", "")
    assert not looks_like_echo("你养了只猫叫团子。", "")


# ---------------- 拒答不能被护栏改坏 ----------------
def test_refusal_not_trimmed():
    """「没记着」是断言缺席，不需要证据；用候选原文补会答非所问。"""
    from agent.companion import trim_reply
    out = trim_reply("这个我还真没记着。", [], "你想去一趟新疆，看喀纳斯的秋天",
                     "我上次去冰岛是什么时候？")
    assert "新疆" not in out
    assert "没记着" in out


def test_refusal_detected():
    from agent.companion import is_refusal
    for s in ("这个我还真没记着", "我翻了翻，真没找到记录", "我这边没存到",
              "不记得了", "想不起来", "记不太清了"):
        assert is_refusal(s), s
    # 正常回答里出现"记"不算拒答
    for s in ("我记得有这么件事：你养了只猫", "你表姐去英国留学了", "你妈在杭州"):
        assert not is_refusal(s), s


# ---------------- v21：兜底引用必须真是被问的那件事 ----------------
def test_trim_reply_wont_cite_unrelated_memory():
    """「我开的什么车」不许被兜底成一条毫不相干的旧记忆。

    这是 v21 实测里最刺眼的一类「无关回复」：事实护栏把违规句裁掉后，程序
    拿 used 候选的原文硬补一句「我记得有这么件事：…」。引的是真记忆，但跟
    问题毫无关系——比老实说不知道更糟。
    """
    from agent.companion import trim_reply
    out = trim_reply("你开的那辆公路车挺好的。", [("你开的那辆公路车挺好的", "x")],
                     "元旦你去了厦门，在鼓浪屿住了两晚", "我开的什么车？")
    assert "厦门" not in out
    assert "公路车" not in out


def test_trim_reply_still_cites_related_memory():
    """同一个问题、候选真的对得上时，照旧引用，别把好兜底一起砍了。"""
    from agent.companion import trim_reply
    out = trim_reply("你开的那辆公路车挺好的。", [("你开的那辆公路车挺好的", "x")],
                     "你买了辆公路车，周末环湖骑行", "我开的什么车？")
    assert "公路车" in out


def test_trim_reply_question_without_evidence_refuses():
    """一句证据都没有时，对问句不能回「嗯，<问题原样>。这个我记下了」。"""
    from agent.companion import trim_reply
    out = trim_reply("你开的那辆公路车挺好的。", [("你开的那辆公路车挺好的", "x")],
                     "", "我开的什么车？")
    assert "我记下了" not in out
    assert "没记着" in out

