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