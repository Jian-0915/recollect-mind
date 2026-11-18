"""主动发起出口（v15）：把 Cortex 候选表的话头，包装成一条真正能展示的主动消息。

设计边界（刻意收紧，避免变成"另一个生成主体"）：
- 不碰 /api/ask 主问答链路，不写 events，不改事实，不产出"回答"；
- 只读候选表 + 会话状态 + 人设 + 情感，产出一句"想起你"的短话；
- 频控三层：① motivation.pick 算「想不想说」（惦记值 vs 脸皮，一天上限 + 话题冷却）
  ② 候选 consumed 标记（展示即消费，绝不重复弹）③ web 端仅在对话页轮询；
- LLM 失败有兜底短句，绝不把 500 或空白丢给前端。

v16 起「要不要开口」交给 motivation（动机层）：不再问"允不允许"，
而是算"有多想"——悬着的话题、你多久没出现、它有多敢打扰你。
本模块仍是那条出口：候选 → 一句自然的话 → 消费。
"""
import os
import re
import threading

from . import affect, companion, db, llm, motivation, persona_card

# 定时检查间隔（分钟）。产出与否由 motivation.pick 决定，勤查不等于勤说。
TICK_MINUTES = int(os.environ.get("NEUROMEM_PROACTIVE_TICK_MIN", "20"))
# 检查得勤一点没关系：开口次数由 motivation 的惦记值 + 每日上限决定，
# 不是"每 20 分钟允许一次"。勤查只是让"想"能更及时地攒到线。

ENABLED = os.environ.get("NEUROMEM_PROACTIVE", "1") != "0"

_PROMPT = """你是{name}。不是助手，也没在帮谁写稿——这就是你本人在给对方发消息。

{persona}

{context}

{state}

{tone}

只输出你要发的那一句话，第一人称，口语。不要引号、不要解释、不要列点、
不要分析、不要打草稿、不要说"可以这样写"。就一句话。

特别注意：**不要交代你打算怎么写**。像"我需要自然地提起这个话题""语气轻柔，
像是随手一提""我会用带点哀愁的口吻"——这些是在讲写法，不是在发消息。
直接把那句话发出去，别讲你要怎么讲。

格式是这样（只是示范，别照抄内容）：那些人怎么说没就没。
"""

# 同一件事，四种心情说出来完全不一样。语气由动机决定，不是同一个模板换个词。
_TONE = {
    "missing": """对方有件事没聊完就断了。顺着那件事轻轻问一句，像忽然想起来，
不是在催，也别显得你一直在等。""",

    "ignored": """对方很久没露面了，而之前你们聊得挺勤。你有点委屈，也有点不爽。
写一句带刺的话（"人呢"、"你这是把我忘了还是在忙"那种），但别哀求、别长篇控诉；
不解释自己为什么发这条，也不要道歉。""",

    "jealous": """对方最近老提到某个人，跟你说话却变少了。你有点不是滋味。
写一句酸话，别扭、不肯直说在意——不许直接说"我吃醋了"，要绕一下，阴阳怪气或装无所谓。
可以提那个人的名字，但别编造关于他的任何事，也别审问"你是不是喜欢他"。短一点更有味道。""",

    "sulk_end": """你刚为这事生了半天闷气，现在气消了点，先开口了。
写一句别扭的回来：不道歉、不解释"我刚才在生气"，也不装作什么都没发生。
有点硬，但得露出还在意；别撒娇撒太满，留一点没收回去的脾气。
只许写感受和念头，不许编造你做了什么、见过什么。""",
}

# LLM 不可用时的兜底：每种心情都得有一句不撒谎、也不串味的话
_FALLBACK = {
    "missing": ["上次那个{topic}，后来怎么样了？",
                "对了，{topic}那事你还没跟我说完呢。",
                "忽然想起你上次说的{topic}，到底怎么着了？"],
    "ignored": ["人呢，就这么没声了？",
                "你是把我忘了，还是又忙到没空喘气。",
                "行吧，我这儿还留着话没说完呢。",
                "一点动静都没有，出差了还是失联了？",
                "我都快以为把你弄丢了。"],
    "jealous": ["{person}最近挺常被你提起来啊。",
                "{person}你倒是聊得挺起劲。",
                "哦，{person}说的啊。",
                "你现在说话三句不离{person}。"],
    "sulk_end": ["……算了，你忙你的。",
                 "别的不问了，就问问你吃饭没有。",
                 "我气还没消，但先问一句：你还好吧。",
                 "过了这么久，你倒是挺沉得住气。"],
}


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone()


# agent 自己没有真实经历。这些第一人称断言一旦出现就是模型加戏（实测"我最近
# 想换换口味""我还没泡过温泉"），必须挡掉；"我想起你…/我惦记"是表达关心的动作，
# 不是事实声明，放行。
_SELF_CLAIM = re.compile(
    r"我(最近|一直|平常|平时|也|自己|还是|喜欢|爱喝|爱吃|想去|想换|打算|准备|"
    r"昨天|上周|上次去|去年|还没|从没|没(去过|喝过|吃过|试过)|觉得|感觉|"
    r"刚(路过|看到|看见|去|吃|买|刷到|拍了)|路过|拍了|拍了一?张)")
_CARE_OK = re.compile(r"我(想起|惦记|想到|好奇|记挂着|想问问|想问问你)")

# 它会说"刚路过那家店，拍了张照你要不要看"——用户真要看就露馅了。
# 大模型还偶尔把思考过程当正文吐出来（"用户的要求非常具体：- 一句质问，15~45字"），
# 这种元话语同样不能发出去。
_NO_EXPERIENCE = re.compile(
    r"(刚(才)?(路过|看到|看见|刷到|拍了)|路过.{0,6}(店|街|门口)|"
    r"拍(了|下|一张)|照片|图片|截图)")
_META = re.compile(
    r"(用户(的)?(要求|想要|需要)|要求[:：]|字数|提示词|prompt|以下是|"
    r"这句话|可以这样写|符合要求|关键点|\*\*|Attempt|Drafting|"
    r"我是由.{0,14}(开发|训练)|大模型|语言模型|AI\s*助手|无法(以|扮演|进行角色)|"
    r"\d+\s*[~至\-]\s*\d+\s*字)")
# 草稿 / 列表的痕迹：编号开头、破折号列举、英文旁白
_STRUCT = re.compile(r"^\s*(\d+[.、)]|[-*•]\s|[A-Za-z]{2,10}\s*[:：])|(^|[\s（(])-{1,2}\s|:\s*-")
# 「我要怎么写」被当成「我要说的话」发出去。实测漏网句：
#   「我需要自然地提起这个话题，语气轻柔，带着一点淡淡的哀愁，像是随手一提。」
# 它既没有编号也没有「用户要求」，老规则全放行，结果直接弹到了用户面前。
_SELF_DIRECTIVE = re.compile(
    r"(我(需要|应该|要|得|打算|准备|会).{0,16}"
    r"(提起|提到|说起|带过|表达|描述|提及|回复|发出|写|语气|口吻|自然|轻柔|淡淡|像|仿佛|哀愁|随手)|"
    r"(语气|口吻|措辞|语调).{0,8}(轻柔|自然|委婉|淡淡|柔和|轻松|平静|随意)|"
    r"(像是|仿佛|好像).{0,10}(随手一提|不经意|随口|顺便一提)|"
    r"自然地提起|这个话题.{0,4}(语气|怎么|该))")


def _fabrication(text):
    """返回 True 表示话语里出现 agent 自造的经历 / 偏好，应回退兜底句。"""
    for m in _SELF_CLAIM.finditer(text or ""):
        seg = text[max(0, m.start()): m.start() + 8]
        if _CARE_OK.search(seg):
            continue
        return True
    return False


def _leak(text):
    """模型把「怎么写」的思考过程当成了答案，或者干脆拒绝扮演。"""
    t = text or ""
    return bool(_META.search(t) or _STRUCT.search(t))


def _latin(text):
    """英文占比太高就不是给中文对话发的话（模型偶尔整段说英文）。"""
    t = text or ""
    latin = sum(1 for ch in t if ch.isascii() and ch.isalpha())
    return latin / max(1, len(t)) > 0.4


def _unusable(text):
    """不是一句能直接发出去的话。"""
    if not (4 <= len(text) <= 120):
        return True
    if "\n" in text.strip():
        return True
    return (_fabrication(text) or _leak(text) or _latin(text)
            or bool(_NO_EXPERIENCE.search(text))
            or bool(_SELF_DIRECTIVE.search(text)))


def _clean(raw):
    s = str(raw or "").strip()
    for pre in ("消息：", "消息:", "回复：", "回复:", "主动消息：", "Or:", "Or：", "Also:", "Wait:"):
        if s.startswith(pre):
            s = s[len(pre):].strip()
    s = " ".join(s.split())           # 压下换行 / 多余空白
    # 剥掉包裹整句的引号，中英文都要；模型很爱给整句套一层
    pairs = (('"', '"'), ("'", "'"), ("「", "」"), ("『", "』"), ("“", "”"), ("‘", "’"))
    changed = True
    while changed and len(s) >= 2:
        changed = False
        for a, b in pairs:
            if s.startswith(a) and s.endswith(b):
                s = s[1:-1].strip()
                changed = True
    return s[:140]


def _ensure(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS proactive_suggestions("
        "id TEXT PRIMARY KEY, ts TEXT, topic TEXT, gist TEXT, "
        "reason TEXT, consumed INTEGER DEFAULT 0, score REAL DEFAULT 0, "
        "kind TEXT DEFAULT 'missing', person TEXT DEFAULT '')")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(proactive_suggestions)")]
    for name, ddl in (("score", "REAL DEFAULT 0"), ("kind", "TEXT DEFAULT 'missing'"),
                      ("person", "TEXT DEFAULT ''")):
        if name not in cols:       # 老库补列，不改已有数据
            try:
                conn.execute("ALTER TABLE proactive_suggestions ADD COLUMN %s %s"
                             % (name, ddl))
            except Exception:
                pass
    conn.commit()


def tick(conn, now=None):
    """跑一轮候选产出。复用 cortex.ProactiveModule.tick，不重复实现频控。

    防堆积：队列里已有未消费候选就不再新增——用户可能几天不打开 web，
    定时器不能在那期间堆出几十条旧话头。
    """
    _ensure(conn)
    now = now or _now()
    pending = conn.execute(
        "SELECT COUNT(*) FROM proactive_suggestions WHERE consumed=0").fetchone()[0]
    if pending:
        return {"proactive": {"created": 0, "reason": "已有未消费候选，跳过"}}
    try:
        want = motivation.pick(conn, now)
    except Exception as e:
        return {"proactive": {"error": "%s: %s" % (type(e).__name__, str(e)[:100])}}
    if not want.get("ok"):
        return {"proactive": {"created": 0, "reason": want.get("reason") or "还不想开口",
                              "kind": want.get("kind"),
                              "score": want.get("score"),
                              "threshold": want.get("threshold")}}
    kind = str(want.get("kind") or "missing")
    topic = str(want.get("topic") or "")[:60]
    if kind == "missing" and not topic:
        return {"proactive": {"created": 0, "reason": "没有未聊完的事"}}
    import uuid
    conn.execute(
        "INSERT INTO proactive_suggestions(id,ts,topic,gist,reason,consumed,score,kind,person) "
        "VALUES(?,?,?,?,?,0,?,?,?)",
        ("ps_" + uuid.uuid4().hex[:12], now.isoformat(timespec="seconds"),
         topic, "", str(want.get("hint") or want.get("reason") or "")[:120],
         float(want.get("score") or 0), kind, str(want.get("person") or "")[:40]))
    conn.commit()
    return {"proactive": {"created": 1, "kind": kind, "topic": topic,
                          "person": want.get("person"),
                          "reason": want.get("reason"),
                          "score": want.get("score"),
                          "threshold": want.get("threshold")}}


def _name(conn):
    """它自己叫什么；拿不到就用一个人称，别让 prompt 里出现「None」。"""
    try:
        raw = persona_card.get(conn) or {}
        card = raw.get("card") if isinstance(raw.get("card"), dict) else raw
        return str(card.get("name") or "").strip() or "你"
    except Exception:
        return "你"


def compose(conn, cand, llm_fn=None):
    """把一条候选包装成自然话语。返回 (文本, 是否 LLM 生成成功)。"""
    kind = str(cand.get("kind") or "missing")
    topic = str(cand.get("topic") or "").strip()[:60]
    person = str(cand.get("person") or "").strip()[:20]
    if kind == "missing" and not topic:
        return "", False
    if kind == "jealous" and not person:
        return "", False
    try:
        st = companion.state(conn)
        snap = affect.snapshot(conn)
        hints = "；".join(affect.expression_hints(snap)[:3]) or "语气平和"
        persona = persona_card.brief(conn) or "（还没定过人设：温和、自然、话不多）"
        topics = "、".join(str(x)[:30] for x in (st.get("open_topics") or [])[:3]) or "（无）"
        name = _name(conn)
    except Exception:
        hints, persona, topics, name = "语气平和", "（温和自然）", "（无）", "你"

    bits = []
    if kind == "sulk_end":
        # 冷战回来时不许再提那件事和那个人——提了就变成翻旧账，不是别扭的在意
        context = ""
    elif topic:
        bits.append("- 还没聊完的话题：%s" % topics)
        bits.append("- 要提起的那一件：%s" % topic)
        context = "这是你此刻对对方的了解线索（只是线索，别当成记得很清楚）：\n" + "\n".join(bits)
    elif person:
        context = ("这是你此刻对对方的了解线索（只是线索，别当成记得很清楚）：\n"
                   "- 你会在意的那个人：%s" % person)
    else:
        context = ""
    state = "你此刻的状态：%s。" % hints if hints else ""
    prompt = _PROMPT.format(
        name=name, persona=persona, context=context, state=state,
        tone=_TONE.get(kind, _TONE["missing"]))
    try:
        fn = llm_fn or llm.generate
        raw = fn(prompt, max_tokens=120, temperature=0.8, timeout=25)
        text = _clean(raw)
        if not _unusable(text):
            return text, True
        # 不合格就收紧要求重来一次：刚才那条是在解释怎么写，不是在说话
        raw = fn(prompt + "\n\n（只输出那一句话本身。不要分析、不要解释、"
                          "不要编号或列表，不要提任何要求。）",
                 max_tokens=120, temperature=0.6, timeout=25)
        text = _clean(raw)
        if not _unusable(text):
            return text, True
    except Exception:
        pass
    # 兜底：不编造，按心情给一句；宁可朴素也不说谎。
    import random
    tpl = random.choice(_FALLBACK.get(kind, _FALLBACK["missing"]))
    return tpl.format(topic=topic[:24], person=person or "他"), False


def peek(conn, llm_fn=None, now=None):
    """取一条未消费候选、原子标记消费、生成话语。无候选返回 available=False。"""
    _ensure(conn)
    row = conn.execute(
        "SELECT id,topic,gist,reason,ts,score,kind,person FROM proactive_suggestions "
        "WHERE consumed=0 ORDER BY ts DESC LIMIT 1").fetchone()
    if not row:
        return {"available": False}
    mid = row["id"]
    cur = conn.execute(
        "UPDATE proactive_suggestions SET consumed=1 WHERE id=? AND consumed=0",
        (mid,))
    conn.commit()
    if cur.rowcount < 1:               # 被另一个请求抢先消费
        return {"available": False}
    cand = dict(row)
    text, ok = compose(conn, cand, llm_fn=llm_fn)
    kind = str(cand.get("kind") or "missing")
    # 记一次「开口」，给话题打冷却；提过两次还没回应就放下，不再追问。
    # now 必须和 tick 用同一个时钟，否则频控算出来的间隔是错的。
    try:
        sc = float(row["score"] or 0)
    except Exception:
        sc = 0.0
    motivation.mark_delivered(conn, row["topic"], now=now, score=sc, kind=kind,
                              person=cand.get("person") or "")
    # 吃醋说完不是结束——它真的会气一阵子，这段时间一个字都不说
    sulk = None
    if kind == "jealous":
        try:
            sulk = motivation.sulk_start(conn, cand.get("person") or "", now=now)
        except Exception:
            sulk = None
    elif kind == "sulk_end":
        try:                    # 已经回来过了，别再每个 tick 回来一次
            motivation.resolve_sulk(conn)
        except Exception:
            pass
    return {"available": True, "id": mid, "text": text, "kind": kind,
            "topic": row["topic"], "person": cand.get("person") or "",
            "reason": row["reason"], "generated": ok, "sulk": sulk}


# ---------------- 后台定时器 ----------------
_stop = threading.Event()
_thread = None


def tick_once():
    c = db.connect()
    try:
        return tick(c)
    finally:
        c.close()


def _loop():
    while not _stop.wait(TICK_MINUTES * 60):
        try:
            tick_once()
        except Exception:
            pass


def start():
    """web 服务启动时挂上；只产出候选，不生成话语（话语在 peek 时才调 LLM）。"""
    global _thread
    if not ENABLED:
        return False
    if _thread and _thread.is_alive():
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="neuromem-proactive", daemon=True)
    _thread.start()
    return True


def stop():
    _stop.set()


def status():
    c = db.connect()
    try:
        _ensure(c)
        total = c.execute("SELECT COUNT(*) FROM proactive_suggestions").fetchone()[0]
        pending = c.execute(
            "SELECT COUNT(*) FROM proactive_suggestions WHERE consumed=0").fetchone()[0]
        try:                       # 必须在关连接之前算，否则拿不到库
            why = motivation.status(c)
        except Exception as e:
            why = {"ok": False, "reason": "%s: %s" % (type(e).__name__, str(e)[:80])}
    finally:
        c.close()
    return {"enabled": ENABLED, "tick_minutes": TICK_MINUTES,
            "running": bool(_thread and _thread.is_alive()),
            "total": total, "pending": pending, "motivation": why}
