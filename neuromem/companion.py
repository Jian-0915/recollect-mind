"""陪伴层：把认知状态接进回答链路，并提供会话态与主动浮现。

职责边界：
- 只处理「状态与表达」，不改事实检索逻辑、不改 events 的写入规则。
- 所有新表都用 CREATE TABLE IF NOT EXISTS，旧库无需手工迁移。
- state() 只读（除幂等建表），可被每次问答安全调用。
"""
import json
import re
import uuid
from datetime import datetime, timezone

from . import config, emotion, identity, persona_card, selfnarrative

_DDL = [
    """CREATE TABLE IF NOT EXISTS companion_state (
         id INTEGER PRIMARY KEY CHECK(id=1),
         last_turn_at TEXT, last_topic TEXT, open_topics TEXT,
         address_style TEXT, boundaries TEXT, surfaced_ids TEXT,
         updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS conversation_turns (
         id TEXT PRIMARY KEY, session_id TEXT, ts TEXT,
         speaker TEXT, text TEXT, gist TEXT, affect_json TEXT,
         event_ids TEXT, created_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_turn_ts ON conversation_turns(ts)",
]


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _uid(prefix):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:12])


def _jload(raw, default):
    try:
        v = json.loads(raw or "")
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


def _ensure(conn):
    # 快路径：表与默认行都齐时只做只读查询，避免每次问答抢写锁（db.py 的老坑）
    cnt = None
    has_row = None
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM sqlite_master WHERE type='table' "
            "AND name IN ('companion_state','conversation_turns')").fetchone()
        has_row = conn.execute("SELECT 1 FROM companion_state WHERE id=1").fetchone()
    except Exception:
        pass
    if cnt is not None and cnt["c"] == 2 and has_row is not None:
        return conn
    for ddl in _DDL:
        conn.execute(ddl)
    conn.execute(
        "INSERT OR IGNORE INTO companion_state(id,open_topics,surfaced_ids) "
        "VALUES(1,'[]','[]')")
    conn.commit()
    return conn


def _one(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchone()
    except Exception:
        return None


def state(conn):
    """汇总此刻陪伴 agent 该知道的背景。"""
    _ensure(conn)
    emo_row = _one(conn, "SELECT valence,arousal,label FROM emotion_state WHERE id=1")
    if emo_row is None:
        try:
            emo_row = emotion.EmotionState(conn).get()
        except Exception:
            emo_row = None
    emo = dict(emo_row) if emo_row is not None else {}

    # v13：自叙事带 scope；counterpart 表示这是关于对方的旧档案，
    # 不是 agent 的第一人称人格，上层不能把它当「我是谁」注入。
    try:
        nar = selfnarrative.get(conn) or {}
    except Exception:
        nar = {}

    sess_row = _one(conn, "SELECT * FROM companion_state WHERE id=1")
    sess = dict(sess_row) if sess_row is not None else {}
    prof = _one(conn, "SELECT profile FROM personas WHERE canonical_name=?",
                (config.COUNTERPART_NAME,))

    return {
        # L0 固定身份（人工设定、清库豁免、不可被对话改写）。放在最前面：
        # 「我是谁 / 我服务谁」必须先于任何关于对方的画像与自叙事。
        "identity": identity.get(conn),
        "identity_block": identity.block(conn),
        # v14：人设卡短版，供 MCP memory_state / 老问答通道读取；
        # 陪伴 agent 主通道用 persona_card.render() 的全文。
        "persona": persona_card.get(conn),
        "persona_block": persona_card.brief(conn),
        "call_user": persona_card.call_user(conn),
        "emotion": emo,
        "narrative": nar.get("text") or "",
        "narrative_at": nar.get("updated_at") or "",
        "narrative_scope": nar.get("scope") or "counterpart",
        "profile": (prof["profile"] if prof else "") or "",
        "last_turn_at": sess.get("last_turn_at") or "",
        "last_topic": sess.get("last_topic") or "",
        "open_topics": _jload(sess.get("open_topics"), []),
        "address_style": sess.get("address_style") or "",
        "boundaries": sess.get("boundaries") or "",
        "surfaced_ids": _jload(sess.get("surfaced_ids"), []),
        "updated_at": sess.get("updated_at") or "",
    }


def format_block(st):
    """把状态拼成给模型看的紧凑文本。"""
    if not st:
        return "（无）"
    lines = []
    emo = st.get("emotion") or {}
    if emo:
        lines.append("情绪基调：%s（效价 %.2f / 唤醒 %.2f）" % (
            emo.get("label") or "平静",
            float(emo.get("valence") or 0),
            float(emo.get("arousal") or 0)))
    if st.get("profile"):
        lines.append("对对方的整体印象（不是我）：" + st["profile"][:180])
    if st.get("narrative"):
        # v13：旧库自述是「关于对方」的，不能写成「他眼里的自己」再当成 agent 人格
        tag = ("我眼里的自己" if st.get("narrative_scope") == "self"
               else "关于对方的旧自述（不是我）")
        lines.append(tag + "：" + st["narrative"][:180])
    if st.get("last_topic"):
        lines.append("上次聊到：" + st["last_topic"][:80])
    ot = st.get("open_topics") or []
    if ot:
        lines.append("还没聊完的事：" + "、".join(str(x)[:40] for x in ot[:3]))
    if st.get("address_style"):
        lines.append("称呼习惯：" + st["address_style"])
    if st.get("boundaries"):
        lines.append("边界：" + st["boundaries"])
    return "\n".join(lines) if lines else "（无）"


def remember_turn(conn, speaker, text, session_id=None, gist=None, affect=None,
                  event_ids=None, topic=None):
    """记录一轮对话原文（不写 events，避免把闲聊沉淀成事实）。"""
    _ensure(conn)
    # 情绪接线：对话的情绪要能推动全局心境，而不是只写进这行日志。
    # 旧版只有 store.remember() 合并事件时更新情绪，纯聊天永远改不了状态。
    if isinstance(affect, dict) and (affect.get("valence") or affect.get("arousal")):
        try:
            emotion.EmotionState(conn).update(
                float(affect.get("valence") or 0.0),
                float(affect.get("arousal") or 0.0),
                weight=config.EMOTION_TURN_WEIGHT)
        except Exception:
            pass
    tid = _uid("turn")
    now = _now()
    conn.execute(
        "INSERT INTO conversation_turns"
        "(id,session_id,ts,speaker,text,gist,affect_json,event_ids,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (tid, session_id or "default", now,
         speaker or config.COUNTERPART_NAME, text or "",
         gist or "", json.dumps(affect or {}, ensure_ascii=False),
         json.dumps(event_ids or [], ensure_ascii=False), now))
    conn.execute(
        "UPDATE companion_state SET last_turn_at=?, last_topic=?, updated_at=? WHERE id=1",
        (now, (topic or gist or text or "")[:80], now))
    conn.commit()
    return tid


def _dedupe_topics(items, limit=8):
    """F7：清掉重复项与「把已有两条用顿号拼起来」的伪话题。

    65 轮长测里 open_topics 累积出「今天为什么特别累、今年冬至温泉该订了」
    这类由 topic 字符串拼接产生的重复条目，以及「黄瓜雷避难所计划」这类
    由塌缩句式生成的话题。
    """
    out = []
    for x in items or []:
        s = str(x or "").strip()
        if not s or s in out:
            continue
        parts = [p.strip() for p in re.split(r"[、,，;；]", s) if p.strip()]
        if len(parts) > 1 and all(p in out for p in parts):
            continue
        out.append(s)
    return out[-limit:]


def update_state(conn, **kw):
    """让上层 agent 维护关系态（未闭合话题 / 称呼 / 边界 / 已提过的记忆）。"""
    _ensure(conn)
    allowed = ("last_topic", "open_topics", "address_style", "boundaries", "surfaced_ids")
    sets, vals = [], []
    for k in allowed:
        if k not in kw:
            continue
        v = kw[k]
        if k == "open_topics" and isinstance(v, list):
            v = _dedupe_topics(v)
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        sets.append(k + "=?")
        vals.append(v)
    if not sets:
        return False
    sets.append("updated_at=?")
    vals.append(_now())
    conn.execute("UPDATE companion_state SET " + ",".join(sets) + " WHERE id=1", vals)
    conn.commit()
    return True


def recent_turns(conn, limit=5):
    _ensure(conn)
    try:
        rows = conn.execute(
            "SELECT ts,speaker,text,gist FROM conversation_turns "
            "ORDER BY ts DESC LIMIT ?", (max(1, min(int(limit), 20)),)).fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


def surface(conn, limit=3, days=None):
    """无 query 的主动浮现：显著性 × 激活 × 近因，避开最近已提过的记忆。"""
    st = state(conn)
    seen = list(st.get("surfaced_ids") or [])
    seen_set = set(seen)
    # F5（2026-11-15 长测后）：「刚聊过什么」也要算作已提过。
    # 原先 seen 只来自 surfaced_ids，而它唯有调用 surface() 才写入；长测里
    # 陪伴侧大多不调 surface，于是上一轮刚聊过的记忆照样排第一（0.730/0.955）。
    try:
        from . import recent as _recent
        seen_set |= _recent.used_ids(
            conn, getattr(config, "RECENT_USED_TURNS", 6))
    except Exception:
        pass
    days = days or config.SURFACE_RECENT_DAYS
    now = datetime.now(timezone.utc).astimezone()
    rows = conn.execute(
        "SELECT id,gist,created_at,salience,activation,affect_json FROM events "
        "WHERE archived=0 AND superseded_by IS NULL").fetchall()
    scored = []
    for r in rows:
        if r["id"] in seen_set:
            continue
        sal = float(r["salience"] or 0)
        act = float(r["activation"] if r["activation"] is not None else 1.0)
        try:
            age = (now - datetime.fromisoformat(r["created_at"])).days
        except Exception:
            age = 999
        rec = max(0.0, 1.0 - max(0, age) / float(days))
        scored.append((0.45 * sal + 0.30 * act + 0.25 * rec, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    picked = scored[:max(1, min(int(limit or 3), 10))]
    update_state(conn, surfaced_ids=(seen + [r["id"] for _, r in picked])[-20:])
    return [{"id": r["id"], "gist": r["gist"], "score": round(s, 4),
             "affect": _jload(r["affect_json"], {})} for s, r in picked]
