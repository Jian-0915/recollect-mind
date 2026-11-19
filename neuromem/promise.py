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
    r"|(?:我(?:去|来|这就|马上|回头|待会儿|先)?查(?:查|一下|看)?)"
)

# 兜底话术（sourceguard._FALLBACK）本身就含承诺，命中它也要兑现。
_FALLBACK_MARK = "等我去查查再告诉你"

# 语气/指代/私人痕迹：命中就不当检索词。
_UNSAFE = re.compile(r"(我|我们|你|你们|他|她|咱|本人|私家|私密|日记|聊天记录|住址|电话|微信)")


def has_promise(reply: str) -> bool:
    """回复里有没有她自己许下的查询承诺。"""
    s = str(reply or "")
    return _FALLBACK_MARK in s or bool(_PROMISE_RE.search(s))


def promised_topic(user_text: str, reply: str, private_terms=()) -> str:
    """从本轮输入抽待查主题；没有承诺或抽不出安全主题就返回空串。

    优先问句主题（「贵州有什么景点」→ 贵州景点），其次实体；
    有 private_terms 先剥，最后再用通用私密词兜底拦一次。
    """
    if not has_promise(reply):
        return ""
    text = str(user_text or "")
    if not text.strip():
        return ""
    terms = [str(t).strip() for t in (private_terms or ()) if str(t).strip()]
    for t in terms:
        text = text.replace(t, " ")
    try:
        topic = intent.extract_info_query(text, private_terms=terms)
        if not topic:
            ents = intent.extract_entities(text, private_terms=terms)
            topic = str(ents[0]).strip() if ents else ""
    except Exception:
        topic = ""
    topic = str(topic or "").strip(" 的了在是和跟与之，。？?！!～~")
    if not (2 <= len(topic) <= 16) or topic in intent._STOP:
        return ""
    if _UNSAFE.search(topic) or any(t and t in topic for t in terms):
        return ""
    return topic
