"""主动发起出口（v15）：把 Cortex 候选表的话头，包装成一条真正能展示的主动消息。

设计边界（刻意收紧，避免变成"另一个生成主体"）：
- 不碰 /api/ask 主问答链路，不写 events，不改事实，不产出"回答"；
- 只读候选表 + 会话状态 + 人设 + 情感，产出一句"想起你"的短话；
- 频控三层：① cortex 复用 affect.should_initiate（能量 / 间隔 / 未闭合话题）
  ② 候选 consumed 标记（展示即消费，绝不重复弹）③ web 端仅在对话页轮询；
- LLM 失败有兜底短句，绝不把 500 或空白丢给前端。

Cortex 的 ProactiveModule 只产出"可以主动提起 X"的建议，never 真正发给用户——
本模块就是缺的那一公里：候选 → 一句自然的话 → 出口。
"""
import os
import re
import threading

from . import affect, companion, db, llm, persona_card

# 定时检查间隔（分钟）；真正是否产出仍由 should_initiate 决定，不会因此骚扰。
TICK_MINUTES = int(os.environ.get("NEUROMEM_PROACTIVE_TICK_MIN", "20"))
ENABLED = os.environ.get("NEUROMEM_PROACTIVE", "1") != "0"

_PROMPT = """{persona}

这是你此刻对对方的了解线索（只是线索，不等于确凿事实，别当成记得很清楚）：
- 还没聊完的话题：{topics}
- 系统建议你提起：{topic}（{reason}）

你的表达状态：{hints}。

请写一句主动发给对方的消息，像朋友过一阵子忽然想起对方、顺着没聊完的事轻轻问一句。
硬性要求：
1. 只输出这句话本身，不要解释、不要加"消息："之类的前缀、不要引号。
2. 20~60 字，一两句，口语，别用"在吗/最近好吗/打扰了"这类空寒暄。
3. 不要声称记得具体细节、人名、数字或日期；用"上次那个…后来呢/怎么样了"这种不坐实的说法。
4. 不要编造新事实，不要复述画像或自叙事，不要一次抛好几个话题，就围绕建议的这一件。
5. 语气和称呼按上面的人设走；如果状态提示能量偏低就短一点、别硬撑热闹。
"""


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone()


# agent 自己没有真实经历。这些第一人称断言一旦出现就是模型加戏（实测"我最近
# 想换换口味""我还没泡过温泉"），必须挡掉；"我想起你…/我惦记"是表达关心的动作，
# 不是事实声明，放行。
_SELF_CLAIM = re.compile(
    r"我(最近|一直|平常|平时|也|自己|还是|喜欢|爱喝|爱吃|想去|想换|打算|准备|"
    r"昨天|上周|上次去|去年|还没|从没|没(去过|喝过|吃过|试过)|觉得|感觉)")
_CARE_OK = re.compile(r"我(想起|惦记|想到|好奇|记挂着|想问问|想问问你)")


def _fabrication(text):
    """返回 True 表示话语里出现 agent 自造的经历 / 偏好，应回退兜底句。"""
    for m in _SELF_CLAIM.finditer(text or ""):
        seg = text[max(0, m.start()): m.start() + 8]
        if _CARE_OK.search(seg):
            continue
        return True
    return False


def _clean(raw):
    s = str(raw or "").strip()
    for pre in ("消息：", "消息:", "回复：", "回复:", "主动消息："):
        if s.startswith(pre):
            s = s[len(pre):].strip()
    s = s.strip().strip('"').strip("'").strip()
    s = " ".join(s.split())           # 压下换行 / 多余空白
    return s[:140]


def _ensure(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS proactive_suggestions("
        "id TEXT PRIMARY KEY, ts TEXT, topic TEXT, gist TEXT, "
        "reason TEXT, consumed INTEGER DEFAULT 0)")
    conn.commit()


def tick(conn, now=None):
    """跑一轮候选产出。复用 cortex.ProactiveModule.tick，不重复实现频控。

    防堆积：队列里已有未消费候选就不再新增——用户可能几天不打开 web，
    定时器不能在那期间堆出几十条旧话头。
    """
    from . import cortex
    _ensure(conn)
    pending = conn.execute(
        "SELECT COUNT(*) FROM proactive_suggestions WHERE consumed=0").fetchone()[0]
    if pending:
        return {"proactive": {"created": 0, "reason": "已有未消费候选，跳过"}}
    mb = type("_MiniBrain", (), {})()
    mb.conn = conn
    plugin = cortex.ProactiveModule(mb)
    try:
        return {"proactive": plugin.tick(now or _now())}
    except Exception as e:
        return {"proactive": {"error": "%s: %s" % (type(e).__name__, str(e)[:100])}}


def compose(conn, cand, llm_fn=None):
    """把一条候选包装成自然话语。返回 (文本, 是否 LLM 生成成功)。"""
    topic = str(cand.get("topic") or "").strip()[:60]
    if not topic:
        return "", False
    try:
        st = companion.state(conn)
        snap = affect.snapshot(conn)
        hints = "；".join(affect.expression_hints(snap)[:3]) or "语气平和"
        persona = persona_card.brief(conn) or "（未设置人设，用温和自然的语气）"
        topics = "、".join(str(x)[:30] for x in (st.get("open_topics") or [])[:3]) or "（无）"
    except Exception:
        hints, persona, topics = "语气平和", "（温和自然）", "（无）"
    prompt = _PROMPT.format(
        persona=persona, topics=topics, topic=topic,
        reason=str(cand.get("reason") or "")[:60], hints=hints)
    try:
        raw = (llm_fn or llm.generate)(prompt, max_tokens=140,
                                       temperature=0.7, timeout=30)
        text = _clean(raw)
        if 4 <= len(text) <= 120 and not _fabrication(text):
            return text, True
    except Exception:
        pass
    # 兜底：不编造，只把话题轻轻挑起来；宁可朴素也不说谎。
    return "上次那个%s，后来怎么样了？" % topic[:24], False


def _retire_topic(conn, topic):
    """主动提起后就不再反复提同一件：从 open_topics 移除该话题。

    只动 companion_state 这张会话状态表，不碰 events / 事实 / 记忆。
    web 的 /api/ask 是只读问答、不写 Brain.commit，所以这一步是主动通道
    自己维护话题队列的唯一动作——否则每次都会提同一个话题。
    """
    topic = str(topic or "").strip()
    if not topic:
        return
    try:
        st = companion.state(conn)
        topics = [str(x).strip() for x in (st.get("open_topics") or [])]
        kept = [x for x in topics if x and x not in topic and topic not in x]
        if len(kept) != len(topics):
            companion.update_state(conn, open_topics=kept)
    except Exception:
        pass


def peek(conn, llm_fn=None):
    """取一条未消费候选、原子标记消费、生成话语。无候选返回 available=False。"""
    _ensure(conn)
    row = conn.execute(
        "SELECT id,topic,gist,reason,ts FROM proactive_suggestions "
        "WHERE consumed=0 AND topic!='' ORDER BY ts DESC LIMIT 1").fetchone()
    if not row:
        return {"available": False}
    mid = row["id"]
    cur = conn.execute(
        "UPDATE proactive_suggestions SET consumed=1 WHERE id=? AND consumed=0",
        (mid,))
    conn.commit()
    if cur.rowcount < 1:               # 被另一个请求抢先消费
        return {"available": False}
    text, ok = compose(conn, dict(row), llm_fn=llm_fn)
    _retire_topic(conn, row["topic"])
    return {"available": True, "id": mid, "text": text,
            "topic": row["topic"], "reason": row["reason"],
            "generated": ok}


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
    finally:
        c.close()
    return {"enabled": ENABLED, "tick_minutes": TICK_MINUTES,
            "running": bool(_thread and _thread.is_alive()),
            "total": total, "pending": pending}
