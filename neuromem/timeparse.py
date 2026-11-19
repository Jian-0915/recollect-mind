"""相对/绝对时间解析：把"昨天/上周三/三天前/2026-11-10"转成日期或区间。"""
import re
from datetime import datetime, timedelta

_WEEK = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
    "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6,
}
_CN_NUM = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _cn2int(s):
    """把 '3' / '三' / '十二' / '二十' 转成整数。"""
    s = str(s).strip()
    if s.isdigit():
        return int(s)
    if s in _CN_NUM:
        return _CN_NUM[s]
    if "十" in s:
        a, _, b = s.partition("十")
        tens = _CN_NUM.get(a, 1) if a else 1
        ones = _CN_NUM.get(b, 0) if b else 0
        return tens * 10 + ones
    return None


def _dstr(dt):
    return dt.strftime("%Y-%m-%d")


def _month_end(y, m):
    return (datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)) - timedelta(days=1)


def _shift_month(dt, n):
    """按日历平移月份，日期溢出时取当月最后一天（1/31 → 2/28）。"""
    y, m = dt.year, dt.month + n
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    last = _month_end(y, m).day
    return datetime(y, m, min(dt.day, last))


_NUM = r"[0-9一二三四五六七八九十两]+"


def parse_date(text, base=None, ctx=None):
    """解析成单日 YYYY-MM-DD；无法确定返回 None。

    ctx：上下文日期（上一条事件），用于"第二天/次日/隔天"这类**句内相对**说法；
    没有 ctx 时按 base（默认今天）算。
    """
    if not text:
        return None
    base = base or datetime.now()
    t = str(text).strip()
    day = datetime(base.year, base.month, base.day)
    ctx_day = datetime(ctx.year, ctx.month, ctx.day) if ctx else day

    m = re.search(r"第\s*(" + _NUM + r")\s*天", t)
    if m:
        n = _cn2int(m.group(1))
        if n:
            return _dstr(ctx_day + timedelta(days=n - 1))  # 第二天 = 上一条 +1
    if "次日" in t or "隔天" in t or "翌日" in t:
        return _dstr(ctx_day + timedelta(days=1))

    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]", t)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", t)
    if m:
        return "%04d-%02d-%02d" % (base.year, int(m.group(1)), int(m.group(2)))

    # 月 / 年级相对（原先只支持天、周）
    m = re.search(r"(" + _NUM + r")\s*个?月前", t)
    if m:
        n = _cn2int(m.group(1))
        if n:
            return _dstr(_shift_month(day, -n))
    if "前年" in t:
        return _dstr(_shift_month(day, -24))
    if "去年" in t:
        return _dstr(_shift_month(day, -12))

    # 英文时间（C7：写入支持英文，时间也要能解析）
    if re.search(r"\byesterday\b", t, re.I):
        return _dstr(day - timedelta(days=1))
    if re.search(r"\btoday\b", t, re.I):
        return _dstr(day)
    if re.search(r"\btomorrow\b", t, re.I):
        return _dstr(day + timedelta(days=1))
    if re.search(r"\bnext\s+week\b", t, re.I):
        return _dstr(day + timedelta(days=7))
    if re.search(r"\blast\s+week\b", t, re.I):
        return _dstr(day - timedelta(days=7))
    if re.search(r"\bnext\s+month\b", t, re.I):
        return _dstr(_shift_month(day, 1))
    if re.search(r"\blast\s+month\b", t, re.I):
        return _dstr(_shift_month(day, -1))

    if "大前天" in t:
        return _dstr(day - timedelta(days=3))
    if "前天" in t:
        return _dstr(day - timedelta(days=2))
    if "昨天" in t or "昨日" in t:
        return _dstr(day - timedelta(days=1))
    if "今天" in t or "今日" in t:
        return _dstr(day)
    if "明天" in t:
        return _dstr(day + timedelta(days=1))
    if "后天" in t:
        return _dstr(day + timedelta(days=2))

    m = re.search(r"(" + _NUM + r")\s*天前", t)
    if m:
        n = _cn2int(m.group(1))
        if n:
            return _dstr(day - timedelta(days=n))
    m = re.search(r"(" + _NUM + r")\s*天后", t)
    if m:
        n = _cn2int(m.group(1))
        if n:
            return _dstr(day + timedelta(days=n))
    m = re.search(r"(" + _NUM + r")\s*周前", t)
    if m:
        n = _cn2int(m.group(1))
        if n:
            return _dstr(day - timedelta(days=n * 7))

    m = re.search(r"(上上|上|本|这|下)?\s*(?:周|星期|礼拜)([一二三四五六日天1-7])", t)
    if m:
        pre = m.group(1) or ""
        wd = _WEEK.get(m.group(2))
        if wd is not None:
            monday = day - timedelta(days=day.weekday())
            offset = {"上上": -14, "上": -7, "": 0, "本": 0, "这": 0, "下": 7}.get(pre, 0)
            return _dstr(monday + timedelta(days=offset + wd))
    return None


def parse_range(text, base=None):
    """解析成 (start, end) 日期区间；无法确定返回 None。"""
    if not text:
        return None
    base = base or datetime.now()
    day = datetime(base.year, base.month, base.day)
    t = str(text).strip()

    d = parse_date(text, base)
    if d:
        return (d, d)

    if "上上周" in t:
        monday = day - timedelta(days=day.weekday())
        start = monday - timedelta(days=14)
        return (_dstr(start), _dstr(start + timedelta(days=6)))
    if "上周" in t:
        monday = day - timedelta(days=day.weekday())
        start = monday - timedelta(days=7)
        return (_dstr(start), _dstr(start + timedelta(days=6)))
    if "这周" in t or "本周" in t:
        monday = day - timedelta(days=day.weekday())
        return (_dstr(monday), _dstr(monday + timedelta(days=6)))
    if "下周" in t:
        monday = day - timedelta(days=day.weekday())
        start = monday + timedelta(days=7)
        return (_dstr(start), _dstr(start + timedelta(days=6)))
    if "上个月" in t or "上月" in t:
        y, mth = (base.year, base.month - 1) if base.month > 1 else (base.year - 1, 12)
        return (_dstr(datetime(y, mth, 1)), _dstr(_month_end(y, mth)))
    if "这个月" in t or "本月" in t:
        return (_dstr(datetime(base.year, base.month, 1)), _dstr(day))
    if "最近" in t:
        return (_dstr(day - timedelta(days=30)), _dstr(day))

    m = re.search(r"(\d{4})年(\d{1,2})月", t)
    if m:
        y, mth = int(m.group(1)), int(m.group(2))
        return (_dstr(datetime(y, mth, 1)), _dstr(_month_end(y, mth)))

    return None


# ---------------- 时段行为（v42） ----------------
# 时间锚点只告诉模型「现在几点」，没告诉它「这个点该说什么话」。实测 2026-11-19
# 上午 08:25 / 08:41，prompt 里明明写着「上午 08:26」，她照旧从记忆里的「睡前读书 /
# 晚安」推出一句「要不要先睡啦，明天再聊～」。所以把时段行为单独做成一块权威指令，
# 并提供程序侧检测，防止模型依然照抄旧习惯。

_BEDTIME_FROM = 22   # 含
_BEDTIME_TO = 5      # 不含；与 prompts._now_block 的「深夜」边界一致

# 只匹配「催对方现在去睡 / 结束对话」，不碰「睡得怎么样」「昨晚睡不好」这类
# 正当提及睡眠的句子，也不碰「我想起你上次说的睡前读书」。
_BEDTIME_TALK_RE = re.compile(
    r"(要不要(先)?睡|先睡(吧|啦|了|咯|喽)|快(去)?睡(吧|啦|了)?|早点(儿)?睡|"
    r"去睡(吧|啦|了|咯|喽)|睡吧|睡啦|睡觉吧|明天再聊|晚安|"
    r"困(了)?(就|那就|就先)?睡|是不是困(了|糊涂)|困糊涂了?)")


def period_of(dt):
    """与 prompts._now_block 共用的时段标签，避免两处边界漂移。"""
    h = dt.hour
    if 5 <= h < 11:
        return "上午"
    if 11 <= h < 13:
        return "中午"
    if 13 <= h < 18:
        return "下午"
    if 18 <= h < 22:
        return "晚上"
    return "深夜"


def bedtime_ok(now=None):
    """此刻说「去睡吧 / 晚安」合不合时宜。"""
    dt = now or datetime.now().astimezone()
    return dt.hour >= _BEDTIME_FROM or dt.hour < _BEDTIME_TO


def has_bedtime_talk(text):
    """这句话是不是在催对方睡觉 / 结束对话。"""
    return bool(_BEDTIME_TALK_RE.search(str(text or "")))


def time_conduct_block(now=None):
    """注入 prompt 的时段行为指令；不传 now 时取本机当前时间。"""
    dt = now or datetime.now().astimezone()
    period = period_of(dt)
    if bedtime_ok(dt):
        return ("【此刻可以自然提睡觉】\n现在是%s %02d:%02d。按气氛自然说晚安就行，"
                "但别反复催。" % (period, dt.hour, dt.minute))
    return ("【此刻的说话时段（程序已核对，必须照做）】\n"
            "现在是%s %02d:%02d，不是睡觉时间。**不许**说「要不要先睡 / 快去睡 / "
            "早点休息 / 晚安 / 明天再聊 / 是不是困了」这类催睡、收尾的话；"
            "早上就自然说早上好，白天就顺着当前的事聊。\n"
            "记忆里出现「晚安 / 睡前 / 昨晚 / 睡眠」是**过去发生过的事**，"
            "不能拿来当作现在该睡觉的依据。\n"
            "只有对方这一轮自己说困 / 想睡 / 要休息，才可以顺着他提睡觉。"
            % (period, dt.hour, dt.minute))