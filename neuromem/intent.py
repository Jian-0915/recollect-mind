"""工具意图层（ROADMAP F1 / F4b）—— 决定「要不要查、查什么」。

核心取舍：本地模型（qwen3.5:4b）的 JSON 输出要靠重试兜底，不能让它承重。
所以这里做成 **程序优先、模型兜底**：

  1. 触发闸：规则从对话里抽有信息量的实体（地名 / 专有名词 / 「XX是什么梗」），
     排除人名、称呼、宠物、亲属，排除私人词。
  2. 知识库先查：已收录就不联网（这是「更聪明」而不是「每次都搜」的关键）。
  3. 模型只在可选环节润色查询词，输出意图 JSON；失败就用规则结果继续，
     绝不因为模型抽风就让整个问答挂掉。

模型永远看不到 MCP 协议，只处理「查什么」这一层。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 地名 / 场所后缀：命中即认为是有信息量的实体，值得查
_PLACE_SUFFIX = ("寨", "镇", "村", "城", "山", "湖", "街", "区", "寺", "桥",
                 "岛", "县", "市", "省", "古镇", "景区", "公园", "广场", "胡同")
# 「是什么梗 / 什么意思」这类显式求解释的句式
_EXPLAIN_RE = re.compile(
    r"[「『《\"“‘]?([\u4e00-\u9fffA-Za-z0-9]{2,12})[」』》\"”’]?\s*"
    r"(是什么梗|是什么意思|是什么|怎么回事|是哪[里个]|是谁|有什么好玩的|怎么玩)")
# 引号里的词（覆盖中英文各种引号，含中文单引号 ‘’）
_QUOTE_RE = re.compile(r"[「『《\"“‘]([^」』》\"”’]{2,20})[」』》\"”‘]")
# 切分标点
_SPLIT_RE = re.compile(r"[，,。.!！?？:：;；、\n\t（）()\[\]【】]+")
# 中文没有词边界：地名后缀往前找时必须剥掉这些口语前缀，否则会把
# 「今天我们聊了乌江寨」整句当成实体。
_NOISE_PREFIX_RE = re.compile(
    r"^(今天|昨天|前天|明天|上周|这周|下周|去年|今年|明年|刚才|刚刚|方才|"
    r"现在|此刻|眼下|这会儿|当前|目前|"
    r"我们|你们|他们|她们|我|你|他|她|您|说|聊|谈|知道|听说|记得|告诉|"
    r"去|到|在|了解|了|的|和|跟|与|把|被|给|也|就|都|还|又|很|太|最|那|这|"
    r"个|些|提|讲|问|想|要|关于|一下|那个|这个|能|可|否|"
    r"除了|另外|还有|说说|讲讲|看看)+")
# 注：2026-11-19 补「知道/听说/上周/去年」等口语前缀 —— 真实对话里
# 「你知道世界第一高桥在哪里吗」被地名后缀路径吞成「你知道世界第一高桥」，
# 「上周去了青岛」吞成整句，拿这种词去查必然 empty，F5 白跑。
# 同日再补「现在/此刻/这会儿/当前/目前」——「你现在知道世界第一高桥在哪里不」
# 这种方言疑问句（「不」作疑问词）里「现在」卡在句首，噪形容词表漏了它，
# 从头连续剥到「现在」就停住，抽成「现在知道世界第一高桥」，F5 又白跑一次。
# noise 从头连续剥：「世界第一高桥」里「世界」不是噪音，剥完停在正确实体。
# 明确的亲属 / 称呼：这类词不该拿去联网。
# 注意 entity.kind_of() 是「不认识就返回 person」的保守设计，其地名表也不含
# 「寨 / 古镇」，直接拿 person 当排除条件会误杀所有地名和梗——这里只按后缀排。
_KIN_SUFFIX = ("哥", "姐", "弟", "妹", "妈", "爸", "爷", "奶", "姑", "舅",
               "姨", "叔", "嫂", "婶", "婆", "公", "朋友", "男友", "女友",
               "老婆", "老公", "对象", "闺蜜", "同事", "同学")
# 模型润色查询词时可能夹带私人信息，这是一层独立兜底（不依赖调用方传词表）
_PRIVATE_QUERY_HINT = re.compile(r"(我|我们|你|你们|她|他|私人|私密|日记|聊天记录|住址|电话)")
# 明显不是检索词的日常词（避免「今天天气」这类被当实体）
_STOP = {
    "今天", "昨天", "明天", "现在", "刚才", "以后", "我们", "你们", "他们",
    "自己", "这个", "那个", "什么", "怎么", "为什么", "因为", "所以", "但是",
    "可以", "应该", "已经", "还是", "就是", "真的", "觉得", "知道", "告诉",
    "一下", "有点", "特别", "非常", "喜欢", "讨厌", "开心", "难过", "谢谢",
    "你好", "晚安", "早安", "哈哈", "嗯嗯", "好吧", "是不是", "有没有",
}


@dataclass
class ResearchIntent:
    need_search: bool = False
    entity: str = ""
    query: str = ""
    reason: str = ""
    private_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"need_search": self.need_search, "entity": self.entity,
                "query": self.query, "reason": self.reason}


def extract_entities(text: str, private_terms=()) -> list[str]:
    """从一句话里抽值得查的实体。规则法，零依赖、零网络。

    只收 2~12 字的中文/字母数字混合词；带地名后缀或出现在显式求解释句式
    或引号里才算候选；人名 / 私人词一律排除。
    """
    s = text or ""
    private = sorted({t.strip() for t in private_terms if t and t.strip()},
                     key=len, reverse=True)
    found: list[str] = []

    def take(word: str):
        w = word.strip()
        # 从候选里剥掉私人词，而不是整条丢弃：
        # 「微微说她去了乌江寨」应留下「乌江寨」，而不是啥也不查
        for p in private:
            w = w.replace(p, " ")
        w = _NOISE_PREFIX_RE.sub("", w.strip()).strip(" 的了在是和跟与之")
        if not (2 <= len(w) <= 12):
            return
        if w in _STOP or w in private:
            return
        if any(p and p in w for p in private):
            return
        if any(w.endswith(k) for k in _KIN_SUFFIX):
            return
        # 复用项目既有的实体分类，但只排「明确是宠物 / 时间」的：
        # entity.kind_of() 对不认识的词一律返回 person，拿 person 当排除条件
        # 会把乌江寨、梗、书名全误杀。人名主要靠 private_terms 词表兜底。
        try:
            from . import entity as _entity
            if _entity.kind_of(w) in ("pet", "time"):
                return
        except Exception:
            pass
        if w not in found:
            found.append(w)

    # 显式求解释：优先，例如「乌江寨是什么地方」「那个梗什么意思」
    for m in _EXPLAIN_RE.finditer(s):
        take(m.group(1))

    # 引号里的词
    for m in _QUOTE_RE.finditer(s):
        take(m.group(1))

    # 带地名后缀的词：在切分后的片段里找后缀起点
    for seg in _SPLIT_RE.split(s):
        seg = seg.strip()
        if not seg or seg in private:
            continue
        for suf in _PLACE_SUFFIX:
            idx = seg.find(suf)
            if idx <= 0:
                continue
            # 从后往前找连续中文，取后缀前的名字部分
            start = idx
            while start > 0 and re.match(r"[\u4e00-\u9fffA-Za-z0-9]", seg[start - 1]):
                start -= 1
            take(seg[start:idx + len(suf)])
            break

    return found


# 信息需求句式：对方在问外部世界的情况，而不是提到某个现成实体。
# F5 旧版只能抽「带地名后缀的专名」，于是「我在想贵州有什么景点」
# 「这边有哪些好玩的」全都漏掉——转头她就用参数知识编景点来答（v44 实测）。
_INFO_KINDS = ("是什么地方", "介绍一下", "值得去", "怎么玩", "景点", "景区",
               "门票", "票价", "多少钱", "多少", "在哪里",
               "好玩的", "好玩", "推荐", "攻略", "有哪些", "有什么")
_INFO_RE = re.compile(r"(.{0,16}?)(" + "|".join(_INFO_KINDS) + r")")
# 只有真在问事才走这条路。「今天去了乌江寨，挺好玩的」是陈述，
# 实体路径已经能覆盖，别再拿「乌江寨好玩」这种词白查一遍。
_QUESTION_RE = re.compile(
    r"[?？]|(?:吗|呢|什么|多少|几|哪些|哪里|哪儿|怎么|如何|推荐|攻略|介绍一下|值得去)")
# 问句里顺带出现的「景点/攻略」要并进检索词，单查一个「贵州」太泛。
_INFO_DETAIL = ("景点", "景区", "攻略", "怎么玩", "值得去", "推荐", "好玩")
_INFO_TAIL_RE = re.compile(
    r"(有什么|有哪些|哪些|什么|哪里|哪儿|怎样|怎么样|如何|想|要去|去玩|"
    r"一下|吗|呢|啊|呀)+$")


def extract_info_query(text: str, private_terms=()) -> str:
    """从「求推荐/求介绍」句式里提炼一个安全的检索主题，不抽到就返回空串。

    只输出主题词（如「贵州景点」），不含人称、称呼、私事——绝不用整段私聊
    当查询词。这是 F5 的补充入口，实体路径照旧，两者去重后一起查。
    """
    s = str(text or "")
    if not s or not _QUESTION_RE.search(s):
        return ""
    m = _INFO_RE.search(s)
    if not m:
        return ""
    head, kind = m.group(1), m.group(2)
    private = [str(p).strip() for p in (private_terms or ()) if str(p).strip()]
    for p in private:
        head = head.replace(p, " ")
    topic = ""
    for seg in reversed(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}", head)):
        w = _NOISE_PREFIX_RE.sub("", seg).strip(" 的了在是和跟与之")
        w = _INFO_TAIL_RE.sub("", w).strip(" 的了在是和跟与之")
        if (2 <= len(w) <= 8 and w not in _STOP
                and not _PRIVATE_QUERY_HINT.search(w)
                and not any(p in w for p in private)):
            topic = w
            break
    if not topic:
        return ""
    # 「景点/攻略」这类词本身要带进查询词才有意义；单查「贵州」太泛。
    for detail in _INFO_DETAIL:
        if detail in s and detail not in topic and len(topic) + len(detail) <= 12:
            topic += detail
            break
    return topic[:16]


def build_intent(text: str, private_terms=(), *, entity: str = "",
                 known: bool = False) -> ResearchIntent:
    """规则构造意图：有实体且知识库没有才需要查。"""
    entities = extract_entities(text, private_terms)
    if entity and entity not in entities:
        entities.insert(0, entity)
    if not entities:
        return ResearchIntent(need_search=False, reason="没有可检索的实体")
    picked = entities[0]
    if known:
        return ResearchIntent(need_search=False, entity=picked,
                              reason="知识库已有可靠信息，直接用旧知识回答")
    return ResearchIntent(
        need_search=True, entity=picked,
        query=f"{picked} 是什么 介绍",
        reason="对话提到有信息量的未知实体",
        private_terms=list(private_terms))


_REFINE_PROMPT = """你在帮一个记忆助手判断要不要联网查资料。
只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。
字段：
  need_search: true/false —— 这个问题是否需要外部网页信息才能答好
  query: 需要联网时，写一个不带私人信息、不带人名、不超过 20 字的检索词
  reason: 一句话理由
对话：{text}
候选实体：{entity}
"""


def refine_intent(intent: ResearchIntent, text: str, *,
                  llm=None) -> ResearchIntent:
    """可选的模型润色环节。任何失败都原样返回规则结果。"""
    if not intent.need_search:
        return intent
    fn = llm
    if fn is None:
        try:
            from . import llm as _llm
            fn = _llm.generate_json
        except Exception:
            return intent
    try:
        data = fn(_REFINE_PROMPT.format(text=text[:300], entity=intent.entity),
                  max_tokens=180, timeout=20, retries=0)
        if not isinstance(data, dict):
            return intent
        q = str(data.get("query") or "").strip()
        if (q and len(q) <= 40
                and not _PRIVATE_QUERY_HINT.search(q)
                and not any(p and p in q for p in intent.private_terms)):
            intent.query = q
    except Exception:
        pass
    return intent
