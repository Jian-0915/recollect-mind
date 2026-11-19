"""识别她自己许下的「我去查查」承诺，抽出安全的待查主题（v45）。

为什么要单独一层：v44 让她在无资料时说「这个我还没查过，我去查查」，
可这句话只是台词——没有任何机制兑现，除非对方再发一句话触发被动查询。
用户的诉求正是「不走被动路径」：她说了要去查，就得自己去查。

安全边界（沿用 F5 红线）：
  - 只把抽出来的主题词入队，绝不把整段私聊塞进队列；
  - 称呼 / 人名 / 宠物名等 private_terms 必须先剥掉；
  - 抽不出主题就返回空，宁可不查也不拿私聊当检索词。
"""
from __future__ import annotations

import re

from . import intent

# 她自己说的话里出现这些，才算「许了愿」。只认第一人称的明确承诺，
# 「你去查查」「要不查查看？」（提议）不算——免得把对话里的每句建议都排队。
_PROMISE_RE = re.compile(
    r"(?:我(?:去|来|这就去|马上就|等会儿|回头|待会儿|先|自己)?"
    r"(?:去|来)?查(?:查|一下|一查|查看|看)?"
    r"(?:再|然后)?(?:告诉|说|跟你讲|发你|给你))"
    # v46：她真实说的话比模板啰嗦——实测 2026-11-19 连续几轮：
    #   「我得先查查才能给你准信」「你稍等我查查，查到就发你～」
    #   「我得把开车时间…捋清楚才能给你准信，查到就第一时间发你」
    # 旧正则只认「我去查查 / 我查查」，全都漏了。补「得先查 / 得把…查 /
    # 等…查 / 查到就…发你 / 稍等我查 / 先查查」这些真实变体。
    r"|(?:我(?:去|来|得|要|先|这就|马上|回头|待会儿|等会儿|打算|准备)?"
    r"(?:把[^，。！？]{0,24})?(?:先)?查(?:查|一下|一查|查看|看)?)"
    r"|(?:等(?:我|会儿|一下|会)?查"
    r"|(?:查到|查好|查完)(?:就|再|第一时间|之后|马上|立刻)*"
    r"(?:告诉|发你|发给你|跟你说|给你|回你))"
)

# 兜底话术（sourceguard._FALLBACK）本身就含承诺，命中它也要兑现。
_FALLBACK_MARK = "等我去查查再告诉你"

# 语气/指代/私人痕迹：命中就不当检索词。
# v46：不能用裸「他/她」——「其他贵州」里的「他」会误杀。只拦明确的
# 人称短语和私事词。
_UNSAFE = re.compile(
    r"(我的|我们的|你的|你们的|他的|她们的|她的|他们的|咱|本人|"
    r"私家|私密|日记|聊天记录|住址|电话|微信|女朋友|男朋友)")


def has_promise(reply: str) -> bool:
    """回复里有没有她自己许下的查询承诺。"""
    s = str(reply or "")
    return _FALLBACK_MARK in s or bool(_PROMISE_RE.search(s))


def _topic_from(text: str, terms: list[str]) -> str:
    """从单句里抽一个安全主题；抽不出返回空。"""
    s = str(text or "")
    if not s.strip():
        return ""
    for t in terms:
        s = s.replace(t, " ")
    try:
        topic = intent.extract_info_query(s, private_terms=terms)
        if not topic:
            ents = intent.extract_entities(s, private_terms=terms)
            topic = str(ents[0]).strip() if ents else ""
    except Exception:
        topic = ""
    topic = str(topic or "").strip(" 的了在是和跟与之，。？?！!～~")
    if not (2 <= len(topic) <= 16) or topic in intent._STOP:
        return ""
    if _UNSAFE.search(topic) or any(t and t in topic for t in terms):
        return ""
    return topic


def promised_topic(user_text: str, reply: str, private_terms=(),
                   history=()) -> str:
    """从本轮输入抽待查主题；没有承诺或抽不出安全主题就返回空串。

    优先本轮问句主题（「贵州有什么景点」→ 贵州景点），其次实体；本轮只回
    「就两天」「那你查到了吗」这类短句、抽不出主题时，再从 history 里最近
    几轮用户原话回溯——实测 2026-11-19 她承诺查贵州自驾线的那几轮，主题全
    在上文，不回溯就一条都排不进队列（这正是「她答应了却不查」的根因）。
    """
    if not has_promise(reply):
        return ""
    terms = [str(t).strip() for t in (private_terms or ()) if str(t).strip()]
    # 本轮优先；抽不出再按 history 从近到远回溯
    candidates = [str(user_text or "")]
    for h in reversed(list(history or ())):
        ht = h if isinstance(h, str) else str(
            (h or {}).get("text") or (h or {}).get("user") or "")
        if ht:
            candidates.append(str(ht))
    seen = set()
    for src in candidates:
        topic = _topic_from(src, terms)
        if topic and topic not in seen:
            return topic
        if src:
            seen.add(src)
    return ""
