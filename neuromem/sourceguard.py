"""外部事实的来源纪律（v44）。

实测（2026-11-19 09:03/09:26）：知识库里只有「乌江寨」和「青岛」的资料，
她却直接说出「黄果树瀑布是亚洲最大的瀑布」「梵净山是世界自然遗产，
红云金顶…」「马岭河峡谷号称地球上最美丽的伤疤」。这些都不是查来的，
是模型参数里的百科知识——但她说出口时没有任何"我没查过"的提示，
对方自然会以为她查过。用户追问「你怎么知道这些景点的，你自己查的吗？」，
她又答「我记得有这么件事：网页搜索功能已经接好了」——把系统能力
说成记忆，更是错上加错。

三道防线：
  1. prompts.SYSTEM：写死三类来源，参数知识不算来源；
  2. capabilities.render_block：禁止把能力状态说成「我记得」；
  3. 本模块：出口兜底——没有资料注入时，强断言不许出口。

只拦"可核查的强断言"，不碰日常闲聊与普通建议，避免把话说残。
"""
from __future__ import annotations

import re

# 强断言：说错就是编造、且对方能一查就发现的那类说法。
# 不收「很好玩 / 值得去 / 挺漂亮」这类主观评价——那不是事实主张。
_CLAIM_RE = re.compile(
    r"世界(?:自然|文化)遗产|非物质文化遗产|"
    r"(?:亚洲|全球|世界|中国|全国)(?:最大|最高|最长|最深|第一|最早|唯一)|"
    r"(?:海拔|面积|落差|深度|长度|高度)\s*[约达有]?\s*\d|"
    r"(?:始|建|形成|opened|成立于)\s*\d{3,4}\s*年?|"
    r"号称|誉为|被称为|排名第一|位列第一|"
    r"\dA级|\d星(?:级)?景区|国家级|省级(?:重点)?"
)

_SENT_SPLIT_RE = re.compile(r"(?<=[，,。！？!?；;～~])")

# 这些句式出现时，说明对方在追问信息来源——更不能含糊过去。
_SOURCE_ASK = ("怎么知道", "怎么知道的", "你查的吗", "自己查的", "真的查过",
               "哪来的", "从哪儿知道", "从哪里知道", "有依据吗", "确定吗")

# 兜底话术：承认没查，不假装查过。
_FALLBACK = "这几个我还没查过，不敢瞎说，等我去查查再告诉你～"


def asks_source(user_text: str) -> bool:
    """本轮是不是在追问「你从哪儿知道的」。"""
    s = str(user_text or "")
    return any(w in s for w in _SOURCE_ASK)


def unsourced_parts(reply: str, *, knowledge_block: str = "") -> list[str]:
    """返回没有资料支撑的强断言分句。

    有【你自己查到的资料】注入时不拦：提示词已要求只说资料里有的，
    这里再做字面判断反而会误删引用了资料的正确回答。
    """
    if not reply or knowledge_block:
        return []
    out: list[str] = []
    for part in _SENT_SPLIT_RE.split(str(reply)):
        seg = part.strip()
        if len(seg) >= 4 and _CLAIM_RE.search(seg):
            out.append(part)
    return out


def guard_reply(reply: str, *, knowledge_block: str = "",
                user_text: str = "") -> tuple[str, bool]:
    """出口兜底：删掉无来源的强断言分句，保留其余原话。

    返回 (新回复, 是否改过)。整句都违规或删完只剩语气词时，换成一句
    诚实承认没查过的话——绝不留下"听起来像查过"的空壳。
    """
    bad = unsourced_parts(reply, knowledge_block=knowledge_block)
    if not bad:
        return reply, False
    parts = _SENT_SPLIT_RE.split(str(reply or ""))
    kept = [p for p in parts if p not in bad]
    out = "".join(kept).strip().rstrip("，,、 ")
    # 只剩「嗯」「那」「哈哈」之类，不如老实承认没查过
    core = re.sub(r"[～~，,。！？!?\s]", "", out)
    if len(core) < 6:
        return _FALLBACK, True
    if out and out[-1] not in "。！？!?～~":
        out += "。"
    # 保留下来的话可能仍在暗示"我知道"，补一句把来源交代清楚
    if asks_source(user_text) or len(bad) >= 1:
        out += _FALLBACK
    return out, True
