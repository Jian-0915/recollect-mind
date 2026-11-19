"""v24：陪伴生成层的「当前日期锚点」——让对话有时间概念。

回归点：写入层 store._EXTRACT_PROMPT 与问答层 answer._PROMPT 都注入了
「今天是 {today}」，唯独陪伴生成层 prompts.SYSTEM 没有，导致 agent 只会照抄
记忆里的「昨晚/上周」却不知道今天几号。本测试锁定修复：两条生成通道都带锚点。
"""
from datetime import datetime

from agent import prompts, companion
from neuromem import timeparse as tp


def test_now_block_contains_date_weekday_period():
    p = prompts._now_block(datetime(2026, 11, 17, 14, 30))
    assert "2026-11-17" in p
    assert "星期二" in p          # 2026-11-17 是周二
    assert "下午" in p            # 14:30
    assert "14:30" in p           # 精确到时分，能回答"现在几点"
    assert "以此为准" in p
    assert "事情发生的日期" in p
    assert "现在几点" in p         # 明确允许直接回答时间问题
    assert "不许说" in p           # 禁止再说"看不见时间"


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


# ---------------- v42：时间要真的影响说话 ----------------
# v24 只让她「知道现在几点」；实测 08:25 / 08:41 她照样说
# 「要不要先睡啦，明天再聊～」——锚点有了，行为没跟上。

MORNING = datetime(2026, 11, 19, 8, 26)
NIGHT = datetime(2026, 11, 19, 23, 10)


def test_period_of_matches_now_block_boundaries():
    for hour, want in [(6, "上午"), (12, "中午"), (15, "下午"),
                       (20, "晚上"), (23, "深夜")]:
        assert tp.period_of(datetime(2026, 11, 19, hour)) == want
        assert want in prompts._now_block(datetime(2026, 11, 19, hour))


def test_bedtime_ok_boundaries():
    assert not tp.bedtime_ok(datetime(2026, 11, 19, 5))
    assert not tp.bedtime_ok(MORNING)
    assert not tp.bedtime_ok(datetime(2026, 11, 19, 21, 59))
    assert tp.bedtime_ok(datetime(2026, 11, 19, 22))
    assert tp.bedtime_ok(NIGHT)
    assert tp.bedtime_ok(datetime(2026, 11, 19, 4, 59))


def test_bedtime_talk_detector_hits_real_case():
    assert tp.has_bedtime_talk("要不要先睡啦，明天再聊～")
    assert tp.has_bedtime_talk("快去睡吧")
    assert tp.has_bedtime_talk("早点睡吧")
    assert tp.has_bedtime_talk("是不是困糊涂啦")
    # 正当提及睡眠不能被误伤
    assert not tp.has_bedtime_talk("睡得怎么样？")
    assert not tp.has_bedtime_talk("我昨晚睡得不好")
    assert not tp.has_bedtime_talk("上次说的睡前读书，后来怎么样了？")
    assert not tp.has_bedtime_talk("早上好呀，今天想聊点什么？")


def test_both_prompt_channels_carry_time_conduct():
    day = prompts.build_prompt(None, "早", [], now=MORNING)
    assert "不是睡觉时间" in day and "必须照做" in day
    assert "不能拿来当作现在该睡觉的依据" in day
    assert prompts.build_prompt_no_brain("id", "早", [], now=MORNING).count(
        "不是睡觉时间") == 1
    night = prompts.build_prompt(None, "晚安", [], now=NIGHT)
    assert "此刻可以自然提睡觉" in night
    assert "不是睡觉时间" not in night


def test_time_safe_reply_trims_bedtime_tail():
    fn = companion.CompanionAgent._time_safe_reply
    out, changed = fn(None, "早上好呀，要不要先睡啦，明天再聊～", "你好", now=MORNING)
    assert changed is True
    assert "早上好" in out and "先睡" not in out and "明天再聊" not in out


def test_time_safe_reply_all_bedtime_falls_back():
    fn = companion.CompanionAgent._time_safe_reply
    out, changed = fn(None, "要不要先睡啦，明天再聊～", "你好", now=MORNING)
    assert changed is True and "睡" not in out and "早上好" in out


def test_time_safe_reply_respects_user_invitation_and_night():
    fn = companion.CompanionAgent._time_safe_reply
    out, changed = fn(None, "好，晚安", "我困了，晚安", now=MORNING)
    assert changed is False and out == "好，晚安"
    out, changed = fn(None, "要不要先睡啦", "你好", now=NIGHT)
    assert changed is False and out == "要不要先睡啦"
