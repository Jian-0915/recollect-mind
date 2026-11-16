"""目标系统：从对话里抽「打算 / 计划 / 准备」，给召回与回答提供"往哪看"。

人脑对应：目标管理（GoalManager）+ 目标驱动注意力。
- 写入记忆时顺带识别目标（规则抽取，不额外调模型）
- 召回：与目标关键词重合的记忆获得小幅加成
- 回答：把进行中的目标注入提示词
"""
import re

from . import config
import uuid
from datetime import datetime

_PLAN_RE = re.compile(
    r"(?:明天|后天|下周|下个月|以后|接下来|今年|这个月)?\s*"
    r"(?:打算|计划|准备|决定|想要|想要去|要)(?P<goal>[^。！？；\n,，]{2,40})")

# 第三人称的事件不是「我」的目标（"小林说要搬走""表哥让我当伴郎"）
_OTHER = ("他", "她", "阿哲", "小林", "小周", "妈妈", "爸爸", "王姨",
          "奶奶", "表哥", "老李", "阿May")
_PLAN_WORDS = ("打算", "计划", "准备", "决定", "想要")

# 明显是即时动作、不构成目标的词
_TRIVIAL = ("吃饭", "睡觉", "回家", "洗澡", "上厕所", "喝水", "上班去",
            "当伴郎", "当伴娘", "做按摩", "按摩", "买菜", "看病", "拿快递")
# 单独一个"要"字太宽（"要当伴郎""要去做按摩"），必须有计划性动作或临近时间
_PLAN_VERBS = ("准备", "考", "学", "找", "换", "搬", "写", "完成", "开始",
               "参加", "申请", "练", "减", "存", "报名", "面试", "旅行",
               "装修", "整理", "出差", "结婚", "辞职", "跳槽")
_FUTURE = ("明天", "后天", "下周", "下个月", "下周末", "以后", "接下来",
           "今年", "这个月", "月底", "假期", "将来", "过几天")


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clean(raw):
    g = (raw or "").strip(" ，,。；:：的")
    # 2 字目标也合法（"搬家""考研"），单字一律不认
    if len(g) < 2 or g in _TRIVIAL:
        return ""
    return g


def extract(text):
    """只抽「我」的目标：第一分句主语必须是本人或无主语。

    第三人称的事（"小林说要搬走""表哥让我当伴郎"）不算目标。
    """
    first = re.split(r"[。！？；\n]", text or "")[0].strip()
    if not first:
        return ""
    first_person = r"我|" + re.escape(config.COUNTERPART_NAME) + "|" + re.escape(config.SELF_NAME)
    if any(w in first for w in _OTHER) and not re.search(first_person, first):
        return ""
    for w in _PLAN_WORDS:
        idx = first.find(w)
        if idx >= 0:
            goal = _clean(first[idx + len(w):])
            if goal:
                return goal
    m = re.match(r"^(?:我|用户)?\s*要\s*(.+)$", first)
    if m:
        rest = m.group(1)
        clause = re.split(r"[，,]", rest)[0]
        # "当伴郎""去做按摩" 是安排/日常，不是目标；要有计划动作或临近时间才认
        if not any(w in clause for w in _FUTURE) and not any(w in clause for w in _PLAN_VERBS):
            return ""
        return _clean(rest)
    return ""


def add(conn, text, source_event=None):
    t = (text or "").strip()
    if len(t) < 3:
        return None
    row = conn.execute(
        "SELECT id FROM goals WHERE text=? AND status='active'", (t,)).fetchone()
    if row:
        return row["id"]
    gid = "goal_" + uuid.uuid4().hex[:10]
    conn.execute(
        "INSERT INTO goals(id,text,status,created_at,done_at,source_event) "
        "VALUES(?,?,'active',?,NULL,?)", (gid, t, _now(), source_event))
    return gid


def observe(conn, event_text, source_event=None):
    """写入记忆后调用：抽到目标就登记。"""
    g = extract(event_text)
    return add(conn, g, source_event) if g else None


def active(conn, limit=10):
    return [dict(r) for r in conn.execute(
        "SELECT id,text,created_at,source_event FROM goals WHERE status='active' "
        "ORDER BY created_at DESC LIMIT ?", (limit,))]


def complete(conn, gid):
    row = conn.execute("SELECT id FROM goals WHERE id=?", (gid,)).fetchone()
    if not row:
        return {"ok": False, "error": "目标不存在"}
    conn.execute("UPDATE goals SET status='done', done_at=? WHERE id=?", (_now(), gid))
    return {"ok": True, "id": gid}


def drop(conn, gid):
    row = conn.execute("SELECT id FROM goals WHERE id=?", (gid,)).fetchone()
    if not row:
        return {"ok": False, "error": "目标不存在"}
    conn.execute("DELETE FROM goals WHERE id=?", (gid,))
    return {"ok": True}


def match_bonus(conn, query):
    """目标驱动注意力：查询与某个目标有足够多 2-gram 重合 → 其来源事件加分。"""
    kws = _bigrams(query)
    if not kws:
        return {}
    out = {}
    for g in active(conn, limit=20):
        gk = _bigrams(g["text"])
        inter = gk & kws
        if not inter:
            continue
        # 一个共同 2-gram 已经算弱相关（"准备"）；两个以上给更高权重
        weight = 0.08 if len(inter) >= 2 else 0.05
        if g.get("source_event"):
            out[g["source_event"]] = max(out.get(g["source_event"], 0.0), weight)
    return out


def _bigrams(text):
    t = re.sub(r"\s+", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1) if len(t) >= 2}