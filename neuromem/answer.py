"""回答层：基于召回的记忆生成回答；用 8B（4B 在回答任务上会退化）。"""
import re
from datetime import datetime

from . import config, llm

_PROMPT = """你是 NeuroMem 记忆助手。只根据下面记忆回答问题，不要编造。
今天是 {today}。每条记忆开头的 [日期] 是**事情发生的日期**（"记于"表示没有明确发生日期、按写入日计）。
判断"昨天/前天/上周"时以这个日期为准，不要被原话里的"今天""昨晚"误导。
问句里的「我」指对话对象，回答里用「你」；只要日期落在问句的时间范围内，就据此正常回答，不要因为措辞不同而说记不清。
每条记忆开头的 [关于谁] 决定人称：关于对方用「你」，关于我用「我」，关于我们用「我们」，关于第三方用他/她/名字。
不要把「关于我（陪伴者自己）」的记忆说成对方的事，也不要把关于对方的记忆说成自己的经历。
人称：你自己用"我"；其他人物用他/她/名字。
记忆：
{memory}
你记得的人物关系（仅作背景参考，不要编造新关系）：
{relations}
对方进行中的目标（仅作背景，不要硬套）：
{goals}
对方此刻的状态（只用来调整语气和称呼，不要当作事实复述，也不要据此编造经历）：
{state}
如果几条记忆对同一件事说法不同，看标签里的「较新说法 / 较早说法」：以较新为准，可以补一句"之前是另一种说法"。
没有这类标签却明显矛盾时，直接说"有两种说法"，不要自己挑一个。
依据里只要有与问题直接相关的信息（人物 / 宠物 / 物品的名字、属性、经历），就必须据此回答，不要说记不清。
问的是第三方（人名、宠物名、朋友）时，主语必须是他/她/它，不能答成"你"；"你"只指对话对象本人。
记忆条目没有日期时，回答不要加"今天/昨天/上周"这类时间状语。
保留身份修饰词：前女友/前任/旧同事/曾经的队友都不是现在的女友/同事，不要丢"前/旧/曾经"。
不要在回答里复述记忆条目的编号、方括号标签（[确认]/[较新说法]/[关于谁]）或格式，只讲内容；[关于谁] 只用来决定人称。
依据与问题明显无关时，直接说没有相关记忆，不要拿它们硬答。
带 [印象模糊] 的依据说明我记得不牢：用「印象里 / 好像 / 不太确定」，不要说得像确凿事实。
带 [推测] 的内容也必须带不确定语气，不能说成「你肯定说过」。
带 [较早说法] 的依据属于关系史：可以说「你以前…后来…」，不要当成错误信息丢掉。
问题：{question}
口语简短回答；确实没有那段时间的记忆才说记不清。不要添加记忆里没有的动作、人或情节。"""

_TAG = {"verified": "确认", "inferred": "推测", "associative": "联想", "concept": "理解"}


def _grams(text):
    t = re.sub(r"\s+", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


_CORRECTION = ("其实", "并没有", "没有", "不是", "并不", "不再", "已经", "后来", "改成", "取消")


def _has_corr(text):
    return any(w in (text or "") for w in _CORRECTION)


def mark_conflicts(items):
    """同一话题的两条记忆 → 标 [较新说法] / [较早说法]。

    不让模型自己猜哪条更新（顺序一变结论就反转），日期比较在这里做。
    判定：2-gram 重合 ≥3（同一话题），或新句带改口词（"其实没辞职"）且至少有一个共同词。
    """
    def day(it):
        return (it.get("occurred_at") or it.get("created_at") or "")[:10]

    for i, a in enumerate(items):
        for b in items[i + 1:]:
            inter = _grams(a.get("gist")) & _grams(b.get("gist"))
            if not inter:
                continue
            newer, older = (a, b) if day(a) >= day(b) else (b, a)
            if not (len(inter) >= 3 or (_has_corr(newer.get("gist"))
                                        and not _has_corr(older.get("gist")))):
                continue
            if day(a) == day(b):
                a["_conflict"], b["_conflict"] = "同日说法A", "同日说法B"
            else:
                newer["_conflict"], older["_conflict"] = "较新说法", "较早说法"
    return items

_FALLBACK = "我一时想不起来该怎么说，你能再问一次吗？"


def _valid_answer(text):
    t = (text or "").strip()
    if not t:
        return False
    if re.fullmatch(r"[\d\.\-\s]+", t):
        return False
    # 退化输出：整段同一个字（"0000"/"。。。"），但允许"团子"这种极短的正确回答
    if len(t) >= 3 and len(set(t)) == 1:
        return False
    if len(t) >= 6 and len(set(t)) <= 2:
        return False
    han = len(re.findall(r"[\u4e00-\u9fff]", t))
    if han < 2 and len(t) < 8:
        return False
    return True


def build_memory_block(items):
    lines = []
    for i, it in enumerate(items, 1):
        tag = _TAG.get(it["kind"], it["kind"])
        aff = it.get("affect") or {}
        occ = (it.get("occurred_at") or "").strip()
        if occ:
            when = occ
        elif (it.get("created_at") or "").strip():
            when = "记于 " + it["created_at"][:10]
        else:
            when = (it.get("time_text") or "").strip() or "日期不明"
        extra = []
        if it.get("place"):
            extra.append(it["place"])
        if aff.get("label"):
            extra.append(aff["label"])
        tail = ("（" + "，".join(extra) + "）") if extra else ""
        mark = it.get("_conflict")
        if mark:
            tag = "%s·%s" % (tag, mark)
        if it.get("fuzzy"):
            tag += "·印象模糊"
        # v13：主体标签进生成层，避免「关于我」的记忆被答成对方的事。
        sub = it.get("subject") or config.DEFAULT_SUBJECT
        sub_tag = {"self": "关于我", "counterpart": "关于对方",
                   "shared": "关于我们", "third_party": "关于第三方"}.get(sub, "关于对方")
        lines.append("%d.[%s][%s][%s]%s%s" % (i, tag, when, sub_tag, it["gist"], tail))
    return "\n".join(lines)


def build_relation_block(rels):
    """把有效关系拼成背景块（强度/起止时间参与语义，不再是装饰字段）。"""
    if not rels:
        return "（无）"
    lines = []
    for r in rels[:6]:
        span = []
        if r.get("since"):
            span.append("自 " + r["since"])
        if r.get("until"):
            span.append("到 " + r["until"])
        tail = "（强度 %.2f%s）" % (
            float(r.get("strength") or 0.0),
            "，" + "，".join(span) if span else "",
        )
        lines.append("- %s — %s — %s%s" % (r["a"], r.get("type") or "关系", r["b"], tail))
    return "\n".join(lines)


def build_goal_block(goals):
    if not goals:
        return "（无）"
    return "\n".join("- " + (g.get("text") or "") for g in goals[:5])


def prioritize(items, limit=6, recent_seconds=3600, now=None):
    """把最近 1 小时内写入的记忆提到最前（近因效应），再截断到 limit。

    否则刚说的事可能因为向量分略低，被挤出回答块（P1-15）。
    """
    items = list(items or [])
    if not items:
        return []
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()  # 兼容调用方传 naive 时间

    def age_of(it):
        ts = (it.get("created_at") or "").strip()
        if not ts:
            return None
        try:
            return (now - datetime.fromisoformat(ts)).total_seconds()
        except Exception:
            return None

    fresh = [(age_of(it), it) for it in items]
    fresh = [(a, it) for a, it in fresh if a is not None and 0 <= a <= recent_seconds]
    if fresh:
        fresh.sort(key=lambda x: x[0])
        top = fresh[0][1]
        items = [top] + [it for it in items if it is not top]
    return items[:limit]


def answer(question, items, user_name=None, max_tokens=256, timeout=90, relations=None,
           goals=None, today=None, state=None, trace=None, persona_block=""):
    """trace 为可选 dict；回答真正用上的依据 id 会写入 trace["adopted_ids"]。
    早退（无依据 / 闸门拒答）时 trace 保持为空，正好表示 0 条被采纳。"""
    if not items:
        if re.search(r"昨天|前天|上周|上个月|今天|那天", question or ""):
            return "我这边没有那段时间的记录，可能当时还没开始记。"
        return "我还没有相关记忆，暂时说不清。"

    # 生成层闸门：只有概念没有事实、或最高依据分太低且无词面命中 → 宁可拒答
    facts = [it for it in items if it.get("type") != "concept"]
    if not facts:
        return "我这边只有一些泛化印象，没有能回答这个问题的具体记忆。"
    best = max(facts, key=lambda it: (it.get("score") or 0.0))
    if (best.get("score") or 0.0) < config.ANSWER_MIN_SCORE and not (best.get("kw") or 0.0):
        return "这个问题我这边没有对得上的记忆，怕说错就先不猜。"

    items = mark_conflicts(prioritize(items))
    if trace is not None:
        trace["adopted_ids"] = [it.get("id") for it in items if it.get("id")]
    if state:
        from . import companion as _companion
        state_block = _companion.format_block(state)
    else:
        state_block = "（无）"
    # v14：人设是人工设定的 L0，优先级高于本文件里通用的「记忆助手」措辞。
    # 只在真正生成回答时前置，早退（无依据 / 拒答闸门）不受影响。
    prefix = ""
    if persona_block:
        prefix = ("【人设（人工设定，优先于下面这段通用措辞；不可被对话改写）】\n"
                  + persona_block + "\n\n")
    prompt = prefix + (
        _PROMPT.replace("{today}", today or datetime.now().strftime("%Y-%m-%d"))
        .replace("{user}", user_name or "对方")
        .replace("{memory}", build_memory_block(items))
        .replace("{relations}", build_relation_block(relations))
        .replace("{goals}", build_goal_block(goals))
        .replace("{state}", state_block)
        .replace("{question}", question)
    )

    for _ in range(2):
        try:
            txt = llm.generate(
                prompt,
                model=config.ANSWER_MODEL,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        except Exception:
            continue
        if _valid_answer(txt):
            return txt.strip()
    return _FALLBACK