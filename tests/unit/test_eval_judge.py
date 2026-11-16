"""评测判分器自身的测试。

判分器错了，整个评测就是假的：会把对的判错（误报），也会把错的判对
（漏报）。这类「测量工具」比被测系统更需要先测——它一旦有偏差，
后面所有基于准确率的决策都跟着偏。
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.eval_memory_quality import attribute, judge  # noqa: E402


def test_judge_requires_all_expect_groups_by_default():
    case = dict(expect=[["云栖"], ["Go"]])
    assert judge("你在云栖科技做后端，主要写 Go", case)[0] is True
    assert judge("你在云栖科技做后端", case)[0] is False   # 缺 Go
    assert judge("你在写 Go", case)[0] is False            # 缺云栖


def test_judge_min_hits_relaxes_for_aggregate_questions():
    """聚合题（我有哪些爱好）答案本来就不唯一，要求全中等于故意判错。"""
    case = dict(min_hits=2, expect=[["烘焙"], ["摄影"], ["瑜伽"]])
    assert judge("你喜欢烘焙和摄影", case)[0] is True
    assert judge("你喜欢烘焙", case)[0] is False
    assert judge("你喜欢烘焙、摄影，还有瑜伽", case)[0] is True


def test_forbid_overrides_correct_answer():
    """旧值被「肯定地说出来」才算错，被否定地说出来是**正确**回答。"""
    case = dict(expect=[["三等奖"]], forbid=["二等奖"])
    assert judge("你拿了三等奖", case)[0] is True
    # 真实基线里模型就是这么答的，旧判分器把它误判成违规
    assert judge("三等奖，我记得是摄影比赛拿的三等奖，不是二等奖。", case)[0] is True
    ok, _, bad = judge("你拿了二等奖", case)
    assert not ok and bad == ["二等奖"]
    # 反过来更正（把错的说成对的）必须判错
    assert judge("不是三等奖，是二等奖", case)[0] is False


def test_forbid_negation_window_is_local():
    """否定豁免只看紧邻几个字，隔远了不能一直豁免下去。"""
    from tools.eval_memory_quality import _forbidden_hit
    assert not _forbidden_hit("不是二等奖", "二等奖")
    assert not _forbidden_hit("拿的是三等奖而非二等奖", "二等奖")
    assert _forbidden_hit("不是三等奖，是二等奖", "二等奖")
    # 同一句里先否定后肯定：第二次出现必须被抓住
    assert _forbidden_hit("不是二等奖，就是二等奖没错", "二等奖")


def test_forbid_only_case_passes_anything_without_forbidden_words():
    """纯 forbid 题（不要求某种表述）只要没说错就算过。"""
    case = dict(expect=[], forbid=["特斯拉"])
    assert judge("这个我不太清楚", case)[0] is True
    assert judge("你开特斯拉", case)[0] is False


def test_attribute_distinguishes_recall_miss_from_unused():
    """修法完全不同：召回没给要改召回，给了没用要改提示词。不能混为一谈。"""
    case = dict(gold=["团子"], expect=[["团子"]])
    cat, _ = attribute(case, ["今天天气不错", "工作很忙"], False, [])
    assert cat == "召回没给"

    cat, _ = attribute(case, ["你养了只橘猫叫团子"], False, [])
    assert cat == "给了没用"

    cat, _ = attribute(case, ["你养了只橘猫叫团子"], True, [])
    assert cat == "ok"


def test_attribute_marks_forbidden_hit_first():
    """命中违禁词优先归因为「说了禁止内容」，否则会被误诊成召回问题。"""
    case = dict(gold=["团子"], expect=[["团子"]], forbid=["年糕"])
    cat, why = attribute(case, ["你养了只橘猫叫团子"], False, ["年糕"])
    assert cat == "说了禁止内容"
    assert "年糕" in why


def test_attribute_handles_no_gold_cases():
    """编造类题目没有 gold 可查，不能因为「候选里找不到」就归因成召回问题。"""
    case = dict(gold=[], expect=[["不记得"]])
    cat, _ = attribute(case, ["一些无关候选"], False, [])
    assert cat == "不该说却说/指代错"
