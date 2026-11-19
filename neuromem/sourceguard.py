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

# v48：资料块只提到 A，模型却展开讲 B/C/D（实测「茅台酒是中国四大名酒之一」
# → 凭空报出汾酒/泸州老窖/西凤酒的各香型）。抓「X 是……的 / X 则是……」这类
# 给具体事物下定义的句式主语；若 X 从未在资料、对话、记忆候选里出现过，
# 就是资料外专名——这种编造一字不差地可被 whitelist 命中，比字面强断言更准。
# 只在本轮确实注入了资料时启用：没资料时已有强断言兜底，别重复拦。
_DEFINE_RE = re.compile(
    r"(?:^|[，,；;。！？!?～~\s])([\u4e00-\u9fff]{2,8}?)"
    r"(?:呀|呢|吧|哦|啊)?(?:则|也|就)?是(?=[\u4e00-\u9fff])"
)
# 这些词作主语是普通口语，不是专名。
_DEFINE_STOP = frozenset(
    "这 那 它 他 她 我 你 您 我们 你们 他们 这人 那人 最 更 还 也 就 都 又 很 太 "
    "关键 重点 问题 原因 办法 结果 意思 情况 事情 东西 玩意 感觉 感觉 其实 确实 "
    "真的 主要 特别 尤其 比如 例如 首先 然后 而且 不过 所以 因为 当然 可能 应该 "
    "一次 一样 一种 一个 这就是 那就 现在 今天 明天 昨天 刚才 目前 通常 一般 "
    "生活 工作 学习 身体 心情 状态 时间 世界 人生 爱情 友情 家人 朋友 "
    "最出名的 出名的 有名的 出名的 最大的 最好的 最棒的 特别的 特别的 "
    "关键的 主要的 重要的 有趣的 好玩的 好看的 好吃的 特别的".split()
)
# 一个分句里凑够这么多个资料外专名下定义，才认定整段是「资料外科普」。
# 单个专名不拦：可能是对话里刚聊过、只是没进白名单的正常词。
_MIN_NOVEL_SUBJECTS = 2

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


def unsourced_defined_subjects(reply: str, *, knowledge_block: str = "",
                               allowed_text: str = "") -> list[str]:
    """v48：找回答里「被下定义、却从没在任何可信来源出现过」的具体专名。

    allowed_text 应含用户最近几轮原话 + 记忆候选 + 资料块，避免把对方自己
    提到的东西当编造。只有资料块非空时才检查——配合提示词处理「资料只提了
    A 的名字，她却展开讲 B/C/D」这种 extrapolation。
    """
    if not reply or not knowledge_block:
        return []
    # 白名单用字符集合即可：专名若是被提过的词，其字串必然出现在来源里。
    seen = str(knowledge_block or "") + str(allowed_text or "")
    out: list[str] = []
    for m in _DEFINE_RE.finditer(str(reply)):
        subj = m.group(1)
        if subj in _DEFINE_STOP or len(subj) < 2:
            continue
        if subj in seen:
            continue
        # 去掉句首被上一段吞进来的连接词残字（「而且茅台酒」「西凤酒则」）
        clean = re.sub(r"^(而且|但是|不过|所以|然后|另外|还有|就是|其实|那个|这个|那|这|而|但|又|还|也|就)+", "", subj)
        if len(clean) < 2 or clean in _DEFINE_STOP or clean in seen:
            continue
        if clean not in out:
            out.append(clean)
    return out


def guard_reply(reply: str, *, knowledge_block: str = "",
                user_text: str = "", allowed_text: str = "") -> tuple[str, bool]:
    """出口兜底：删掉无来源的强断言 / 资料外专名，保留其余原话。

    返回 (新回复, 是否改过)。整句都违规或删完只剩语气词时，换成一句
    诚实承认没查过的话——绝不留下"听起来像查过"的空壳。

    allowed_text：用户最近几轮原话 + 记忆候选，供「资料外专名」检测排除
    对方自己提过的词；不传也能用，只是白名单窄一些。
    """
    # 1) 资料外科普：资料只提了 A，却给多个没提过的 B/C/D 下定义。
    #    这是整段编造，不保留——留一句「我还没查过」比拼凑原话更诚实。
    novel = unsourced_defined_subjects(
        reply, knowledge_block=knowledge_block, allowed_text=allowed_text)
    if len(novel) >= _MIN_NOVEL_SUBJECTS:
        return _FALLBACK, True

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
