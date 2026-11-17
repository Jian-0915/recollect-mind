"""v24：陪伴生成层的「当前日期锚点」——让对话有时间概念。

回归点：写入层 store._EXTRACT_PROMPT 与问答层 answer._PROMPT 都注入了
「今天是 {today}」，唯独陪伴生成层 prompts.SYSTEM 没有，导致 agent 只会照抄
记忆里的「昨晚/上周」却不知道今天几号。本测试锁定修复：两条生成通道都带锚点。
"""
from datetime import datetime

from agent import prompts


def test_now_block_contains_date_weekday_period():
    p = prompts._now_block(datetime(2026, 11, 17, 14, 30))
    assert "2026-11-17" in p
    assert "星期二" in p          # 2026-11-17 是周二
    assert "下午" in p            # 14:30
    assert "以此为准" in p
    assert "事情发生的日期" in p


def test_now_block_period_boundaries():
    cases = [
        (6, "上午"), (12, "中午"), (15, "下午"), (20, "晚上"), (23, "深夜"),
    ]
    for hour, want in cases:
        p = prompts._now_block(datetime(2026, 11, 17, hour, 0))
        assert want in p, (hour, want)


def test_build_prompt_has_time_anchor():
    p = prompts.build_prompt(None, "我昨天去哪了", [],
                             now=datetime(2026, 11, 17, 9, 0))
    assert "【现在的时间" in p
    assert "2026-11-17" in p
    assert "上午" in p
    # 规则段仍在（不能被锚点挤掉）
    assert "记忆条目开头的 [日期]" in p


def test_build_prompt_guarded_has_time_anchor():
    p = prompts.build_prompt(None, "上周", [], fact_guard=True,
                             now=datetime(2026, 11, 17, 9, 0))
    assert "【现在的时间" in p and "2026-11-17" in p
    assert "claims" in p          # fact_guard 契约仍在


def test_build_prompt_no_brain_has_time_anchor():
    p = prompts.build_prompt_no_brain("identity", "你好", [],
                                      now=datetime(2026, 11, 17, 9, 0))
    assert "【现在的时间" in p and "2026-11-17" in p


def test_build_prompt_default_now():
    p = prompts.build_prompt(None, "x", [])
    assert "当前日期：" in p and "现在的时间" in p
