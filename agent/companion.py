"""CompanionAgent：第一个真正把 Brain 接进底层的陪伴 agent。

一轮的完整链路（全部真实发生，没有模拟回传）：

    text → Brain.turn() → ContextPack → LLM 生成结构化输出
         → agent 自己给出 used_ids / remember / affect
         → 白名单校验 → Brain.remember() 沉淀事实
         → Brain.commit() 回写 L2 采纳 + 再巩固 + 会话 + 情感 + 插件
         → trace（可观测）

刻意不做的事：不调用 /api/ask、不调 memory_ask（避免出现两个生成主体）；
不经过 MCP（MCP 留给外部模型兼容）；不提供任何改写 L0 固定身份的入口。
默认数据库是独立的 ``data/agent_lab.db``，主库绝不被本模块写入。
"""
import json
import os
import re
import time
from collections import deque
from pathlib import Path

from neuromem import config, db, identity, llm, persona_card
from neuromem import motivation as nm_motivation
from neuromem import store as nm_store
from neuromem.brain import Brain

from . import prompts
from .bootstrap import ensure_lab_settings, is_project_lab_db, sync_l0_from_main

BASE_DIR = Path(__file__).resolve().parent.parent
LAB_DB = BASE_DIR / "data" / "agent_lab.db"
LAB_TRACE = BASE_DIR / "data" / "agent_lab.trace.jsonl"

# 上下文窗口：默认保留最近 6 轮（= 12 条）。原来硬编码 4 条 = 只有 2 轮，
# 一旦对话稍微展开，上一轮说过的话就被挤出窗口，模型只能靠事件层回忆；
# 而事件层又是有损的（写入时判断过「值不值得记」）。原话本来全在库里，
# 所以窗口长度按条数可配，进程重启后还能从 conversation_turns 回填。
HISTORY_TURNS = max(1, int(os.environ.get("NEUROMEM_HISTORY_TURNS", "6") or 6))
HISTORY_MAXLEN = HISTORY_TURNS * 2
# 回填时超过这个间隔就在提示词里标出「那是之前的事」，避免隔天接着上一轮的语气说
HISTORY_STALE_HOURS = float(os.environ.get("NEUROMEM_HISTORY_STALE_HOURS", "3") or 3)

_TAG_RE = re.compile(
    r"\[(?:id:[^\]]*|关于[^\]]*|确认|较新说法|印象模糊|候选记忆|固定身份|"
    r"本轮提示|当下状态|还没聊完|进行中的目标|契约)[^\]]*\]")
_BAD_FACT = ("[", "]", "id:", "used_ids", "候选", "记忆模块", "context_text",
             "outcome", "reply", "系统提示", "请输出")

# --- v14 生成层护栏（全部可用环境变量关掉，专供同剧本 A/B 隔离变量）---
# FACT_GUARD：claims 白名单校验 + 事实收敛重写 + 裁剪
FACT_GUARD = os.environ.get("NEUROMEM_FACT_GUARD", "1") != "0"
# PARSE_RETRY：结构化输出解析失败时把错误反馈给模型重试一次
PARSE_RETRY = os.environ.get("NEUROMEM_PARSE_RETRY", "1") != "0"
# REMEMBER_FALLBACK：模型一条事实都没提取、但用户明显在讲自己的事时兜底提取。
# 2026-11-16 实测：写不写过去**完全由模型自己判断**「值不值得长期记」，
# 结果是「金毛今天撞我的腿」这种当天小事被整轮丢掉，下一轮再问就答不上来
# （turn 13 remember=[]，turn 14 回答「我好像没记着」）。所以兜底不再只认
# 时间词（旧逻辑只白名单 上周/昨天/上次…，「今天」不在里面就永不触发），
# 改成「第一人称陈述句」即可兜底，宁可记碎，也不能漏。
REMEMBER_FALLBACK = os.environ.get("NEUROMEM_REMEMBER_FALLBACK", "1") != "0"
# 强时间线索：有它说明在讲具体某件事，兜底更该触发
_PAST_RE = re.compile(
    r"上周|上个月|上个星期|上个礼拜|昨天|昨晚|前天|今早|今晚|今天|"
    r"刚才|刚刚|刚才|上次|去年|前年|之前|以前|那天|那时|这几天|这两天|"
    r"最近|下周|明天|明年|去了|去过|搬到|开始|决定|打算|准备|改成|换成")
# 问句 / 请求 / 指令：不是陈述，不能当事实
_QM_RE = re.compile(r"[?？]|什么|哪|谁|怎么|怎样|多少|哪些|是否|"
                    r"有没有|干嘛|多久|几[个次天年趟位]|吗|呢|吧|嘛|好不好")
_CMD_HEAD_RE = re.compile(r"^(?:请|帮我|麻烦|你|别|不要|记住|记一下|提醒我|"
                          r"告诉我|跟我说|说说|讲讲|聊聊|来点|继续)")
# 把对方的口头请求误当事实：句子里在要求我记事 / 求认同
_ASK_AI_RE = re.compile(r"(?:你|帮我)(?:记住|记一下|记得|知道|告诉我|提醒|"
                        r"能不能|会不会|可不可以|应该)")
_GREET_ONLY = {"你好", "您好", "hi", "hello", "在吗", "在么", "谢谢", "再见",
               "早", "晚安", "嗯", "哦", "好的", "行"}


def make_llm(model=None, provider=None, temperature=0.7):
    """返回一个 callable(prompt)->str。默认走项目已配置的 LLM。

    provider="ollama" 时直连本地 Ollama（不走 settings 的云端配置），
    便于用 qwen3:8b 之类做离线对照。
    """
    if provider == "ollama":
        import requests
        name = model or "qwen3:8b"

        def _local(prompt):
            r = requests.post(
                "http://127.0.0.1:11434/api/generate",
                json={"model": name, "prompt": prompt, "stream": False,
                      "think": False, "format": "json",
                      "options": {"temperature": temperature,
                                  "num_predict": 700}},
                timeout=180)
            r.raise_for_status()
            return r.json().get("response", "")
        return _local

    def _cloud(prompt):
        # think=False：陪伴对话明确关思考（v21）。云端思考模型会先写一大段
        # 内心独白再开口，实测首字延迟翻倍，-lite 甚至把输出预算全花在思考上、
        # 正文被截断成非法 JSON，整轮降级成「我这边没组织好」——用户感受到的
        # 就是"回复老是无关"。关掉后同一提示词的回答更短、更贴题。
        return llm.generate(prompt, model=model, json_mode=True,
                            max_tokens=800, temperature=temperature,
                            timeout=90, think=False)
    return _cloud


def _balanced_json(s, start):
    """从第一个 { 起做括号配平，避免正文后面杂字里的 } 干扰提取。"""
    depth, in_str, esc = 0, False, False
    for k in range(start, len(s)):
        ch = s[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:k + 1]
    return None


def parse_output(raw):
    """稳健解析模型输出；失败时降级成纯文本回复并标记 parse_ok=False。

    v14：括号配平扫描 + 尾逗号容错，减少小模型输出前后杂字/多一个逗号时
    整轮降级成「我这边没组织好」。
    """
    s = str(raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.M).strip()
    i = s.find("{")
    frag = _balanced_json(s, i) if i >= 0 else None
    if frag is None:
        j = s.rfind("}")
        frag = s[i:j + 1] if i >= 0 and j > i else ""
    frag = re.sub(r",\s*,+", ",", frag)          # 连续逗号
    frag = re.sub(r",\s*([}\]])", r"\1", frag)   # 尾逗号
    data = None
    if frag:
        try:
            data = json.loads(frag)
        except json.JSONDecodeError:
            data = None
    if isinstance(data, dict) and data.get("reply") is not None:
        return data, True
    # 模型没按 JSON 输出：不能把整段 JSON 当台词念出来
    fallback = "" if "{" in s else s
    return {"reply": fallback or "我这边没组织好，你再说一遍好吗？",
            "used_ids": [], "context_text": "", "remember": [],
            "affect": {}, "outcome": "refused"}, False


def clean_reply(text):
    s = str(text or "").strip()
    s = _TAG_RE.sub("", s)
    s = re.sub(r"```[a-z]*|```", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s.strip('"').strip()


# 冷战期兜底：模型不照「只说五个字」的时候，替它把话说短。
# 两组分开是为了别串味——极冷是敷衍，松动是还端着但能接话。
_SULK_COLD = ("嗯。", "哦。", "随你。", "行吧。")
_SULK_TEPID = ("随你怎么想。", "我没什么好说的。", "你忙你的。", "嗯，我听着。")
_SENT_RE = re.compile(r"[^。！？!?…\n]+[。！？!?…]*")
# 「知道啦，……」这种短开场白，重复上一轮就是在敷衍了事
_OPEN_RE = re.compile(r"^([^，。！？,.!?]{1,6})[，,]\s*")


def _drop_repeat_open(reply, prev):
    """上一轮已经用这个开头了，就别再拿它垫一句。

    冷战期的话本来就短，连着两轮「知道啦」听起来不是生气，是根本没看
    对方说什么。只删开头那一小截，剩下的照说。
    """
    s = str(reply or "").strip()
    p = str(prev or "").strip()
    if not s or not p:
        return s
    m = _OPEN_RE.match(s)
    if not m:
        return s
    head = m.group(1).strip()
    if len(head) < 2 or not p.startswith(head):
        return s
    rest = s[m.end():].strip()
    return rest if len(rest) >= 4 else s


def _avoid_repeat(reply, history, tone=0.0):
    """实在说不出新的，也别一字不差地重复上一句。

    普通闲聊有一次重试纠偏（build_repeat_prompt），但冷战/被戳时的短句
    重试往往还是同一句——搜索空间就那么点。实测连着两轮都是「行，那我不
    吵你了。」，第二遍读起来就像它根本没听。这时候宁可换一句。
    """
    seen = {_norm(x) for x in _recent_agent_replies(history, 3)}
    if not reply or _norm(reply) not in seen or tone <= 0.15:
        return reply
    for cand in (_SULK_COLD if tone >= 0.7 else _SULK_TEPID) + _SULK_TEPID:
        if _norm(cand) not in seen:
            return cand
    return reply


def shape_sulk(reply, temper, prev="", allow_q=False):
    """按脸色把回复裁到该有的长度和语气。

    只靠提示词约束不住：模型被要求「只回五个字」，实测照样写一整句完整的话，
    读起来像客服在赌气。所以出口再按温度裁：冷就只留第一句，太长就换兜底句。
    allow_q：冷战期问句等于主动往下聊，要挡；被顶了一句时反问是自然的，放行。
    """
    t = float(temper or 0)
    s = str(reply or "").strip()
    if not s or t <= 0.15:
        return s
    s = _drop_repeat_open(s, prev)
    if t >= 0.4:
        sents = [x.strip() for x in _SENT_RE.findall(s) if x.strip()]
        if sents:
            s = sents[0]
    has_q = ("？" in s) or ("?" in s)
    if t >= 0.7:
        s = (s.replace("！", "。").replace("!", "。")
              .replace("～", "").replace("~", ""))
        if len(s) > 14 or (has_q and not allow_q):
            s = _SULK_COLD[len(s) % len(_SULK_COLD)]
    elif t >= 0.4:
        if len(s) > 26 or (has_q and not allow_q):
            s = _SULK_TEPID[len(s) % len(_SULK_TEPID)]
    elif len(s) > 36:
        s = _SULK_TEPID[len(s) % len(_SULK_TEPID)]
    return s


# 模型偶尔不按 JSON 输出，把「提示词里的格式要求」或「对方的问题」原样吐回来。
# 这类文本一旦经 parse_output 的纯文本兜底变成台词，就是最糟的一种错——
# 对方看到的是自己的问题被复述一遍（实测 27 题里 6 题这样）。
# 判定放在台词出口前，宁可标记成"没组织好"也不要念出去。
_ECHO_MARKERS = ("你要说出口的话", "不要带任何标签", "自然口语", "1~3 句",
                 "只输出画像", "输出 JSON")


# 承认"没有这条记忆"的回答。这类话本身不需要证据支撑——它断言的是**缺席**，
# 而不是某个事实。护栏必须能识别它，否则会把一个正确回答改坏：
# 实测「我上次去冰岛是什么时候？」模型答「这个我还真没记着」，
# 判分器把这句话当成"未申报的断言"删掉，再用候选原文补一句
# 「我记得有这么件事：你想去一趟新疆」——从"老实说不知道"变成"答非所问"。
_REFUSAL_RE = re.compile(
    r"没(?:有)?(?:记着|记住|印象|存到|存着|找到|记录|提过|听你|说过)|"
    r"不记得|想不起来|想不起|记不清|记不太清|没有这条")


def is_refusal(text):
    return bool(_REFUSAL_RE.search(str(text or "")))


def _norm_side(s):
    """比对用归一：去掉标点，并把「我/俺/您」按对话者的口吻折成「你」。"""
    t = _norm(s)
    return re.sub(r"[我俺]", "你", t.replace("您", "你"))


def looks_like_echo(text, user_text=""):
    """这段"台词"是否其实是复述问题 / 提示词片段，而不是回答。"""
    s = clean_reply(text)
    if not s:
        return False
    q = clean_reply(user_text)
    if q:
        ns, nq = _norm_side(s), _norm_side(q)
        if ns and ns == nq:
            return True
        # 只比问题主体、几乎没多出内容：口吻从「我」换成「你」也算复述
        if nq and len(ns) <= len(nq) + 6 and nq in ns:
            return True
    if any(m in s for m in _ECHO_MARKERS):
        return True
    return False


def normalize_remember(raw, reply, user_text=None):
    """把模型给出的「新事实」清洗成 (text, subject) 列表。

    这层过滤是必要的：真实测试里模型很容易把复述、工具痕迹、自己的回复
    当事实写进来。store.py 里还有更严的 F1~F7 拒收规则，两层叠加。

    ``user_text`` 给出时额外挡一种：**跟对方这轮原话字面几乎不沾边**的条目。
    实测「金毛今天干嘛了」这种纯追问轮，模型会把上一轮的记忆（甚至候选里的
    旧事）当成新事实再交一遍，库里只会多一条判重合并。真事一定能在对方原话里
    找到字面影子，所以这条闸门不会误伤新事实。
    """
    items = raw if isinstance(raw, list) else []
    out = []
    for item in items[:6]:
        subject = None
        if isinstance(item, dict):
            subject = item.get("subject")
            item = item.get("text")
        text = str(item or "").strip()
        if not (4 <= len(text) <= 120):
            continue
        if any(b in text for b in _BAD_FACT):
            continue
        if reply and (text == reply or (len(text) > 20 and text in reply)):
            continue
        if user_text and not claim_grounded(text, [user_text], 0.34):
            continue
        if subject is not None:
            subject = identity.normalize_subject(subject)
        out.append((text, subject))
    return out


# ---------------- v14 事实约束工具 ----------------
def _norm(s):
    return re.sub(r"[\s，。！？、,.!?：:；;\"'（）()\[\]【】…—-]", "",
                  str(s or ""))


def _last_agent_reply(history):
    """最近一次「我自己」说过的话；没有就返回空串。"""
    for h in reversed(list(history or [])):
        if str(h.get("role")) == "agent":
            return str(h.get("text") or "")
    return ""


# 「我上一句/刚才说了什么」：答案唯一且确定，交给模型反而会从 12 条窗口里
# 挑错一条（实测 11:49 答成了 11:38 的内容）。命中时用程序把答案钉死。
_LAST_UTTER_RE = re.compile(
    r"我(?:刚才|刚刚|上一句|上句|上一次|上一句话)|上一句我|我刚说|我前面说")


def _is_last_utterance_query(text):
    t = str(text or "").strip()
    if not (2 <= len(t) <= 24):
        return False
    if not _LAST_UTTER_RE.search(t):
        return False
    return bool(re.search(r"什么|啥|哪|吗|呢|说了|讲的|说的", t))


def _last_user_utterance(history, counterpart):
    """最近一条「对方」说的话（当前这轮还没进 history）。"""
    for h in reversed(list(history or [])):
        if str(h.get("role")) == counterpart:
            return str(h.get("text") or "")
    return ""


def _recent_agent_replies(history, n=3):
    """最近 n 轮我说过的话。

    只比「上一轮」会漏掉跨轮复读：实测 11:38 与 11:49 两轮回复一字不差，
    中间隔了一轮不同的话，所以按「上一条」比对时完全没触发。
    """
    out = []
    for h in reversed(list(history or [])):
        if str(h.get("role")) == "agent":
            txt = str(h.get("text") or "")
            if txt:
                out.append(txt)
            if len(out) >= max(1, n):
                break
    return out


def _bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else {s}


# 判「问的是不是这件事」时要忽略的虚词/代词。中文口语里「我/的/什么」几乎
# 每句都有，留着它们会让任何一条候选都算"相关"。
_Q_STOPCH = set("我你他她它的了是吗呢吧啊嘛呀么哪什么怎谁多少几在会要就都也"
                "又和与跟这那有个些位次趟件种点上下时候说给把被对从向")


def _content_chars(s):
    return {c for c in _norm(s) if "\u4e00" <= c <= "\u9fff" and c not in _Q_STOPCH}


def _related(context_text, user_text):
    """引用的旧事和这句问话有没有共同的具体字眼。

    兜底引用必须真是「对方问的那件事」。实测「我上次去冰岛是什么时候」被兜底
    成「我记得有这么件事：你有个闺蜜叫阿岚」——引的确实是真记忆，但和问题毫无
    关系，比老实说不知道更糟；「我开的什么车」被接成「那周末环湖骑行挺爽的吧」
    是同一类错。所以先看字面有没有交集，没有就宁可拒答。

    判定用两条并集：有共同二字组（「养宠物」↔「养了只狗」），或问句里的实词
    有过半出现在候选里（「什么车」↔「公路车」）。只靠二字组太严——「什么车」
    和「公路车」没有共同二字组，会把这个正确兜底也砍掉。
    """
    a, b = _norm(context_text), _norm(user_text)
    if not a:
        return False
    if not b:
        return True      # 拿不到问句就无从判断相关性，按旧行为引用
    if _bigrams(a) & _bigrams(b):
        return True
    qc = _content_chars(b)
    if not qc:
        return True      # 整句都是虚词（「你怎么样」），没有可比的实词
    hit = sum(1 for c in qc if c in a)
    return hit / len(qc) >= 0.5


def candidate_evidence(pack, used, user_text, history):
    """本轮可以当事实说出口的原文：用户原话 + 最近原文 + 被采用候选的原文。"""
    ev = [str(user_text or "")]
    ev += [str(h.get("text") or "") for h in (history or [])]
    for c in ((pack.get("candidates") or []) if pack else []):
        if c.get("id") in used:
            ev.append(str(c.get("gist") or ""))
            ev += [str(f) for f in (c.get("fragments") or [])]
    return [x for x in ev if x]


def normalize_claims(raw):
    """claims -> [(陈述原文, 依据 id)]；只收结构完整的条目。"""
    out = []
    for item in (raw if isinstance(raw, list) else [])[:8]:
        if not isinstance(item, dict):
            continue
        t = str(item.get("text") or "").strip()
        sid = str(item.get("source_id") or "").strip()
        if t and sid:
            out.append((t[:160], sid))
    return out


def claim_grounded(text, evidence, min_ratio=0.6):
    """陈述是否能在依据原文里找到字面支撑（子串或 2-gram 覆盖 ≥ min_ratio）。

    刻意不用向量相似度：事实校验要的是「有没有说过这句话」，不是「像不像」。
    """
    t = _norm(text)
    if not t:
        return False
    ev = [_norm(x) for x in (evidence or []) if x]
    joined = "".join(ev)
    if t in joined:
        return True
    bg = _bigrams(t)
    if not bg:
        return t in joined
    best = 0.0
    for e in ev:
        eb = _bigrams(e)
        if eb:
            best = max(best, len(bg & eb) / len(bg))
    return best >= min_ratio


# 「本轮原话」可以当依据：对方这一轮亲口说的话，本身就是最硬的证据。
# 旧实现要求每个 claim 的 source_id 必须是候选 id，于是「你八月去了大理」
# ——对方刚说完的事实——也被判成「无依据」裁掉，用户感受到的是「我刚说的它
# 当没听见」。这是 2026-11-16 实测里最刺眼的一条。
_USER_SOURCE = {"user", "user_text", "本轮原话", "对方原话", "对方本轮原话"}


def validate_claims(claims, used, evidence, user_text=None):
    """返回 (是否合规, 违规 [(text, reason)], 是否缺申报)。

    ``user_text`` 提供时，source_id 为 "user" 的 claim 只跟对方本轮原话核对。
    """
    bad = []
    for text, sid in (claims or []):
        if sid in _USER_SOURCE:
            pool = [user_text] if user_text else (evidence or [])
            if not claim_grounded(text, pool):
                bad.append((text, "对方本轮原话里找不到这句话"))
        elif sid not in used:
            bad.append((text, "source_id 不在本轮采用的候选里"))
        elif not claim_grounded(text, evidence):
            bad.append((text, "依据原文里找不到这句话"))
    missing = bool(used) and not (claims or [])
    return (not bad and not missing), bad, missing


def trim_reply(reply, bad_claims, context_text="", user_text=""):
    """程序兜底：删掉含违规陈述的句子；删空了就只剩有依据的原话。"""
    # 「没记着 / 没存到」这类回答断言的是"没有这条记忆"，不需要证据；
    # 用候选原文去补它会直接把正确回答变成答非所问。原样保留。
    if is_refusal(reply):
        return clean_reply(reply)
    sents = [s for s in re.split(r"(?<=[。！？!?；;])", str(reply or ""))
             if s.strip()]
    needles = [_norm(t)[:3] for t, _ in (bad_claims or [])
               if len(_norm(t)) >= 3]
    keep = [s for s in sents if not any(n and n in _norm(s) for n in needles)]
    out = clean_reply("".join(keep))
    if out:
        return out
    if context_text and _related(context_text, user_text):
        # 这里引的是候选原文（由程序从 gist 取，不再由模型自述）。
        # 「我记得你说过」这种归因句式会出错——候选可能写的是别人的事。
        return "我记得有这么件事：%s" % context_text
    # 连旧记忆都没得引（或引了也不搭）时，退到对方本轮原话：这是唯一还站得住的依据。
    # 但对方说的是问句时不能这么接——「嗯，我开的什么车？。这个我记下了」是把
    # 问题当陈述复述一遍，比拒答更像没在听。
    u = clean_reply(str(user_text or ""))
    if u and not _QM_RE.search(u):
        return "嗯，%s。这个我记下了，你接着说。" % u.rstrip("。！？!?；;")
    return "这个我这边没记着，你再跟我说说？"


def fallback_fact(user_text):
    """模型一条都没提取时，兜底存「对方关于自己的一句陈述」。

    2026-11-16：旧版要求命中 ``_PAST_RE`` 的时间词才兜底，「金毛今天撞我的腿」
    因为表里没有「今天」被整条丢掉，下一轮就答不上来。现在改成按**陈述句**
    判定：把话按逗号切开，挑一句最像「讲自己」的留下。

    只补「提取」不替 agent 判断「说不说出口」，仍然挡掉问句 / 请求 / 寒暄 /
    工具痕迹 / 冲着 AI 提的要求。
    """
    t = str(user_text or "").strip()
    # 「记一下：今天上午把网页接通了」——前缀是指令，后面才是要记的事。
    # 不能因为整句以指令开头就整条丢掉：这类句子恰恰是最明确要记的。
    t = re.sub(r"^(?:帮我)?(?:记一下|记住|记下来|记着|备注一下|记录一下|mark)"
               r"[：:，,\s]*", "", t, flags=re.I).strip()
    if not (6 <= len(t) <= 140):
        return ""
    if any(b in t for b in _BAD_FACT):
        return ""
    if t.lower() in _GREET_ONLY:
        return ""
    if _ASK_AI_RE.search(t) and not _PAST_RE.search(t):
        return ""      # 「你能不能记住我生日」——这是在要求我，不是在陈述

    best_i, best_score = -1, -1
    segs = [x.strip() for x in re.split(r"[，,；;。！!\n]+", t) if x.strip()]

    def _ok(seg):
        if not (4 <= len(seg) <= 100):
            return False
        if seg.lower() in _GREET_ONLY:
            return False
        if _QM_RE.search(seg) or _CMD_HEAD_RE.search(seg):
            return False
        return not any(b in seg for b in _BAD_FACT)

    for i, seg in enumerate(segs):
        if not _ok(seg):
            continue
        has_me = "我" in seg
        has_time = bool(_PAST_RE.search(seg))
        if not (has_me or has_time):
            continue
        # 有「我」又有时间线索的最像一件具体经历；都没有主语但带时间线索的
        # （「金毛今天撞了腿」）也算——中文口语经常省略主语。
        score = (2 if has_me else 0) + (1 if has_time else 0)
        if score > best_score:
            best_i, best_score = i, score
    if best_i < 0:
        return ""
    # 找到主句后把后面还在同一件事里的分句一起带上，否则「我开始听古典乐」
    # 会把「巴赫的哥德堡变奏曲」丢掉——细节恰恰是回忆时要用的部分。
    buf = segs[best_i]
    for seg in segs[best_i + 1:]:
        if len(seg) < 2 or _QM_RE.search(seg) or _CMD_HEAD_RE.search(seg):
            break
        if any(b in seg for b in _BAD_FACT):
            break
        if len(buf) + 1 + len(seg) > 100:
            break
        buf += "，" + seg
    return buf[:100]


class CompanionAgent:
    """陪伴 agent；``use_brain=False`` 即无记忆对照组。"""

    def __init__(self, *, db_path=None, session_id="lab", model=None,
                 provider=None, temperature=0.7, llm_fn=None,
                 use_brain=True, counterpart=None, trace_path=LAB_TRACE,
                 max_history=None, modules=None):
        # 默认独立实验库；显式 NEUROMEM_DB 可整体切换。主库绝不被写入。
        self.db_path = Path(db_path or os.environ.get("NEUROMEM_DB") or LAB_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        ensure_lab_settings(self.db_path)   # 配置只读复制，保证 LLM 可用
        # L0 由后台统一管理：实验库每次启动从主库同步宪法与人设，
        # 否则「后台改了人设，agent 还是默认人格」。临时单测库不参与。
        if is_project_lab_db(self.db_path):
            sync_l0_from_main(self.db_path)
        self.session_id = session_id
        self.counterpart = counterpart or config.COUNTERPART_NAME
        self.use_brain = bool(use_brain)
        self.llm_fn = llm_fn or make_llm(model, provider, temperature)
        self.history = deque(maxlen=int(max_history or HISTORY_MAXLEN))
        self.history_warmed = 0
        self.trace = []
        self.last_pack = None
        self.last_candidates = []
        self.trace_path = Path(trace_path) if trace_path else None
        self.brain = (Brain(path=str(self.db_path), session_id=session_id,
                            counterpart=counterpart, modules=modules)
                      if self.use_brain else None)
        # v14：人设与宪法都是 L0，两边（接记忆 / 无记忆对照）读同一份，
        # 保证 A/B 公平；agent 侧只读，绝不提供写入口。
        src = self.brain.conn if self.brain else db.connect(str(self.db_path))
        try:
            self.identity_block = identity.block(src)
            self.persona_block = persona_card.render(src)
            self.counterpart = (counterpart
                                or (self.brain.counterpart if self.brain else
                                    persona_card.call_user(src))
                                or config.COUNTERPART_NAME)
        finally:
            if not self.brain:
                src.close()
        self._warmup_history()

    def _warmup_history(self):
        """从 conversation_turns 回填最近几轮原话。

        内存 deque 在进程重启后是空的，而 web 通道是常驻单例——重启一次就
        「不记得上一句说过什么」。原话一直存在库里，只是没人读回来。
        对照组（use_brain=False）刻意不回填：它本来就该没有记忆。
        """
        if not self.brain or not self.history.maxlen:
            return
        try:
            rows = list(self.brain.conn.execute(
                "SELECT speaker, text, ts FROM conversation_turns "
                "WHERE session_id=? ORDER BY ts DESC, rowid DESC LIMIT ?",
                (self.session_id, self.history.maxlen)))
        except Exception:
            return
        for r in reversed(rows):
            txt = str(r["text"] or "").strip()
            if not txt:
                continue
            role = "agent" if (r["speaker"] or "") == "agent" else self.counterpart
            self.history.append({"role": role, "text": txt,
                                 "ts": str(r["ts"] or "")})
        self.history_warmed = len(self.history)

    # ---------------- 生成与收敛 ----------------
    def _generate(self, prompt, user_text=""):
        """一次生成 + 解析；解析失败时把错误反馈给模型重试一次。"""
        try:
            raw = self.llm_fn(prompt)
            err = ""
        except Exception as e:                      # 生成失败也要留痕，不静默
            raw, err = "", "%s: %s" % (type(e).__name__, str(e)[:160])
        out, ok = parse_output(raw)
        # 复述问题 / 念提示词：模型偶发不按 JSON 输出时，纯文本兜底会把
        # 「对方的原话」当成台词塞回去（实测「你现在住哪儿？」这种答案）。
        # 这不是弱回答，是最刺眼的一种错，宁可退回"没组织好"。
        if ok and looks_like_echo(out.get("reply"), user_text):
            ok = False
            err = err or "echo"
        retried = retry_ok = False
        if not ok and PARSE_RETRY:
            retried = True
            reason = ("JSON 解析失败；原始输出：" + str(raw)[:120]
                      + ("；" + err if err else ""))
            try:
                raw2 = self.llm_fn(prompts.build_repair_prompt(prompt, reason))
                out2, ok2 = parse_output(raw2)
                if ok2 and not looks_like_echo(out2.get("reply"), user_text):
                    out, ok, err, retry_ok, raw = out2, True, "", True, raw2
            except Exception as e2:
                err = "%s: %s" % (type(e2).__name__, str(e2)[:160])
        if not ok:
            # 兜底必须是"没组织好"，不能把复述/提示词片段当台词
            out = {"reply": "我这边没组织好，你再说一遍好吗？",
                   "used_ids": [], "context_text": "", "remember": [],
                   "affect": {}, "outcome": "refused"}
        return out, ok, err, retried, retry_ok, raw

    def _converge(self, reply, out, pack, used, text, bad, missing, ctx):
        """事实违规后的收敛：一次重写 → 仍违规则程序裁剪。

        返回 (reply, claims, rewritten, trimmed)。重写只动 reply / claims，
        不推翻本轮已做好的 used_ids / remember / affect 判断。
        """
        # 先说"没记着"再被改写：等于让模型把一句老实话改成编一条记忆。
        if is_refusal(reply):
            return reply, [], False, False
        ev = candidate_evidence(pack, used, text, self.history)

        def _safe(final):
            """兜底产物同样要过复读检查：模型在"重写"里又复述问题时会到这一步。"""
            if looks_like_echo(final, text):
                # 手里没有能回答这个问题的记忆，就别硬塞一条无关的
                return "这个我这边没记着，你再跟我说说？"
            return final

        try:
            raw2 = self.llm_fn(prompts.build_grounding_prompt(
                reply, bad, ev, text, missing))
            out2, ok2 = parse_output(raw2)
            if ok2:
                new_reply = clean_reply(out2.get("reply"))
                if looks_like_echo(new_reply, text):
                    new_reply = ""
                new_claims = normalize_claims(out2.get("claims"))
                ok3, bad2, _ = validate_claims(new_claims, used, ev, text)
                if new_reply and ok3:
                    return new_reply, new_claims, True, False
                # 仍缺申报但没有抓到违规句子：放行重写结果，不裁剪，避免误伤
                if new_reply and not bad2:
                    return new_reply, new_claims, True, False
                if new_reply and bad2:
                    return _safe(trim_reply(new_reply, bad2, ctx, text)), [], True, True
        except Exception:
            pass
        if not bad:        # 只是没申报、没有具体违规片段：原样保留
            return reply, [], False, False
        return _safe(trim_reply(reply, bad, ctx, text)), [], False, True

    # ---------------- 一轮真实对话 ----------------
    def step(self, text):
        text = str(text or "").strip()
        t0 = time.time()
        pack = self.brain.turn(text, history=self.history) if self.brain else None
        # 留下本轮候选的完整快照：评测与排查都要回答「是没召回，还是召回了没用」。
        # 只留判分需要的字段，避免整包 embedding 驻留内存。
        self.last_pack = pack
        self.last_candidates = [
            {"id": c.get("id"), "gist": c.get("gist"), "score": c.get("score")}
            for c in ((pack or {}).get("candidates") or [])
        ]
        guard_on = bool(self.brain and FACT_GUARD)
        # 冷战期：先问一句「现在什么脸色」，再决定这轮话该多短、多刺。
        # 记账（轮数 / 哄的程度）在这一步完成，所以同一轮里 temperature 是稳定的。
        sulk_ctx = None
        provoke = 0.0
        if self.brain:
            try:
                sulk_ctx = nm_motivation.chat_state(self.brain.conn, text)
                # 没在冷战，但对方这轮可能在试我会不会介意（「我要冷落你」）。
                # 冷战优先：气还没消的时候，先按脸色说话，别叠加别的情绪。
                if not (sulk_ctx or {}).get("active"):
                    provoke = nm_motivation.provoked(text)
            except Exception:
                sulk_ctx, provoke = None, 0.0
        prompt = (prompts.build_prompt(pack, text, self.history, self.counterpart,
                                       fact_guard=guard_on, sulk=sulk_ctx,
                                       provoke=provoke)
                  if self.brain else
                  prompts.build_prompt_no_brain(
                      self.identity_block, text, self.history, self.counterpart,
                      self.persona_block))
        # 「我上一句说什么」答案唯一：把原话钉进提示词，模型不许再自己挑。
        if _is_last_utterance_query(text):
            _lu = _last_user_utterance(self.history, self.counterpart)
            if _lu:
                prompt += (
                    "\n\n【确定性提示（程序已核对，以此为准）】\n"
                    "对方问的是「上一句」。上一句原话是：「%s」。\n"
                    "只回答这一句；不要用更早的轮次代替，也不要补充别的。" % _lu[:120])

        (out, parse_ok, llm_error, parse_retried, parse_retry_ok,
         raw) = self._generate(prompt, user_text=text)
        # 这一轮模型到底有没有在思考：写进 trace，别让「关掉了」只停留在配置上。
        think = dict(getattr(llm, "LAST_THINK", None) or {})

        # 整句复读上一轮：实测 09:18 与 09:20 两轮回复一字不差，用户感受就是
        # 「它根本没在听我说话」。这里只纠偏一次措辞，不推翻本轮的判断。
        repeat_retried = False
        seen_replies = {_norm(x) for x in _recent_agent_replies(self.history, 3)}
        if (guard_on and seen_replies and out.get("reply")
                and _norm(out.get("reply")) in seen_replies):
            try:
                raw_r = self.llm_fn(prompts.build_repeat_prompt(
                    prompt, clean_reply(out.get("reply"))))
                out_r, ok_r = parse_output(raw_r)
                if ok_r and _norm(out_r.get("reply")) not in seen_replies:
                    merged = dict(out)
                    merged.update({k: v for k, v in out_r.items() if v not in (None, "", [])})
                    out = merged
                    repeat_retried = True
            except Exception:
                pass

        reply = clean_reply(out.get("reply"))
        cand_ids = pack.candidate_ids() if pack else []
        valid = set(cand_ids)
        raw_used = [str(x) for x in (out.get("used_ids") or []) if x]
        used = list(dict.fromkeys(x for x in raw_used if x in valid))
        invalid = [x for x in raw_used if x not in valid]
        # context_text 改由程序生成：取实际采用候选的 gist 原文。
        # 模型自述版本既多花 100 字输出（约 2 秒），又会丢主语——
        # 实测写出「你养的，叫布丁」，而这条记忆其实是「阿岚养了只狗叫布丁」。
        ctx = "；".join(
            str(c.get("gist") or "") for c in ((pack or {}).get("candidates") or [])
            if c.get("id") in used)[:300]
        outcome = "refused" if out.get("outcome") == "refused" else "answered"
        affect_raw = out.get("affect") or {}

        # --- P1 事实约束：claims 白名单 + 字面依据校验 + 收敛重写 ---
        claims, bad, claims_missing = [], [], False
        fact_violation = fact_rewritten = fact_trimmed = False
        if guard_on and used:
            claims = normalize_claims(out.get("claims"))
            ok_claims, bad, claims_missing = validate_claims(
                claims, used,
                candidate_evidence(pack, used, text, self.history), text)
            if not ok_claims:
                fact_violation = True
                reply, claims, fact_rewritten, fact_trimmed = self._converge(
                    reply, out, pack, used, text, bad, claims_missing, ctx)

        # 冷战期最后一道手：模型不照长度来时，出口按脸色裁。
        # 必须放在事实收敛之后——converge 会把话重写回正常长度。
        tone = ((sulk_ctx or {}).get("temper") or 0.0
                if (sulk_ctx or {}).get("active") else (provoke or 0.0))
        if tone > 0.15:
            reply = shape_sulk(reply, tone, prev=_last_agent_reply(self.history),
                               allow_q=not (sulk_ctx or {}).get("active"))
        reply = _avoid_repeat(reply, self.history, tone)

        facts = (normalize_remember(out.get("remember"), reply, text)
                 if self.brain else [])
        fallback_on = False
        if self.brain and REMEMBER_FALLBACK and not facts:
            fb = fallback_fact(text)
            if fb:
                facts = [(fb, None)]
                fallback_on = True
        remembered_ids, fact_errors = [], []
        # store.remember 判重时也返回**旧事件 id**，上层光看 id 分不清"新增"还是
        # "合并到已有记忆"。这里按 store 的写入计数区分，供回执如实显示——
        # 说"写入 1 条"而库里没增加，是比不显示更糟的误导。
        added = deduped = backdated = 0
        for fact, sub in facts:
            try:
                ids = self.brain.remember(fact, subject=sub,
                                          affect_input=affect_raw or None)
                remembered_ids.extend(ids or [])
                lw = getattr(nm_store, "LAST_WRITE", {})
                added += int(lw.get("new") or 0)       # 每次 remember 内部会归零
                deduped += int(lw.get("dup") or 0)
                backdated += int(lw.get("backdated") or 0)
            except Exception as e:
                fact_errors.append(str(e)[:100])

        commit = {}
        if self.brain:
            try:
                commit = self.brain.commit(
                    reply, used_ids=used, query_text=text, context_text=ctx,
                    outcome=outcome, affect_input=affect_raw,
                    topic=str(out.get("topic") or "")[:60] or None,
                    open_topic=str(out.get("open_topic") or "")[:60] or None,
                    user_text=text)
            except Exception as e:
                commit = {"error": "%s: %s" % (type(e).__name__, str(e)[:160])}

        _now = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.history.append({"role": self.counterpart, "text": text, "ts": _now})
        self.history.append({"role": "agent", "text": reply, "ts": _now})

        snap = (commit.get("affect") or {}) if commit else {}
        row = {
            "turn": len(self.trace) + 1, "session_id": self.session_id,
            "use_brain": self.use_brain, "user": text, "reply": reply,
            "query_rewrite": (pack.get("query_rewrite") or "") if pack else "",
            "candidate_count": len(cand_ids),
            "candidates": [{"id": c.get("id"), "subject": c.get("subject"),
                            "score": round(float(c.get("score") or 0), 3)}
                           for c in (pack.get("candidates") or [])] if pack else [],
            "used_ids": used, "invalid_used_ids": invalid,
            "context_text": ctx, "context_ok": bool(ctx),
            "claims": claims, "fact_guard": guard_on,
            "fact_violation": fact_violation,
            "fact_bad": [[t, why] for t, why in bad],
            "claims_missing": claims_missing,
            "fact_rewritten": fact_rewritten, "fact_trimmed": fact_trimmed,
            "outcome": outcome, "parse_ok": parse_ok, "llm_error": llm_error,
            "think": think,
            "parse_retried": parse_retried, "parse_retry_ok": parse_retry_ok,
            "repeat_retried": repeat_retried,
            "remember_fallback": fallback_on,
            "remember": [f for f, _ in facts], "remembered_ids": remembered_ids,
            "remember_added": added, "remember_deduped": deduped,
            "remember_backdated": backdated,
            "fact_errors": fact_errors, "affect": affect_raw,
            "mood_top": snap.get("top_label") or "", "hints": (pack.get("hints") or []) if pack else [],
            "module_notes": (pack.get("module_notes") or {}) if pack else {},
            "initiate": (pack.get("initiate") or {}) if pack else {},
            "sulk": (sulk_ctx or {}) if sulk_ctx else {},
            "provoke": provoke or 0.0,
            "commit": {k: v for k, v in commit.items() if k != "affect"},
            "elapsed_ms": int((time.time() - t0) * 1000),
            "raw_head": str(raw or "")[:200],
        }
        self.trace.append(row)
        return row

    # ---------------- 批量 / 交互 ----------------
    def run_script(self, turns, *, quiet=False):
        for t in turns:
            say = t.get("say") if isinstance(t, dict) else str(t)
            row = self.step(say)
            if not quiet:
                print("[%02d] %s" % (row["turn"], say))
                print("     → %s" % row["reply"])
                print("     候选 %d / 采用 %d / 写入 %d / 情绪 %s / %s%s%s" % (
                    row["candidate_count"], len(row["used_ids"]),
                    len(row["remembered_ids"]), row["mood_top"] or "-",
                    row["outcome"],
                    "" if row["parse_ok"] else " / 解析降级",
                    " / 事实收敛" if row.get("fact_violation") else ""))
        return self.trace

    def surface(self, limit=3):
        return self.brain.surface(limit=limit) if self.brain else []

    def tick(self):
        return self.brain.tick() if self.brain else {}

    def report(self):
        from .report import lab_report
        return lab_report(self.db_path, self.trace, use_brain=self.use_brain)

    def save_trace(self):
        if not self.trace_path:
            return None
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trace_path.open("w", encoding="utf-8") as f:
            for row in self.trace:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return str(self.trace_path)

    def close(self):
        if self.brain:
            self.brain.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
