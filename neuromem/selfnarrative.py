"""自我叙事：生成第一人称自述（我是谁 / 我在意什么 / 近况）。

v13 主体翻转前的坑：这里默认「取事件最多的人物」当叙事主体，且让 LLM
从一堆关于对方的记忆里猜「我是谁」，于是产出第三人称旧人设与
"Thinking Process" 噪声，被 companion.state() 当成 agent 的人格注入。

现在：
- 主体由 L0 固定身份层给定（identity.get()['profile']['who']），不再让 LLM 猜。
- 优先只用 ``events.subject IN ('self','shared')`` 的经历，scope='self'。
- 老库全被迁移成 counterpart，尚无「我的经历」时允许回退到旧人物事件，
  但结果标 scope='counterpart'，上层（Brain / format_block）明确知道
  它**不是我的人格**，只作关于对方的背景，不注入第一人称上下文。
"""
from datetime import datetime, timezone

from . import config, identity, llm

_PROMPT = """固定身份（不可更改）：{who}
根据下面关于「{scope_label}」的经历，用第一人称写一段自述（100字内）。
{scope_rule}
不要编造经历里没有的内容，不要输出 "Thinking Process" 之类的过程说明或解释。
经历：
{events}
只输出自述文字，不要解释。"""

_SCOPE = {
    "self": (
        "我（陪伴者自己）",
        "说明我是谁、我在意什么、我最近怎么样；「我」只指陪伴者本人。",
    ),
    "counterpart": (
        "对话对象",
        "只写我陪对方经历过什么、我对对方的了解；不要把对方的经历写成我自己的经历。",
    ),
}


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _ensure(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS self_narrative("
        "id INTEGER PRIMARY KEY CHECK(id=1), text TEXT, updated_at TEXT, "
        "scope TEXT DEFAULT 'counterpart')"
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(self_narrative)")}
    if "scope" not in cols:
        conn.execute("ALTER TABLE self_narrative ADD COLUMN scope TEXT "
                     "DEFAULT 'counterpart'")
    return conn


def _self_events(conn, limit=24):
    """v12+：只取属于 agent 自己 / 共同经历的 event gist。"""
    try:
        return [r["gist"] for r in conn.execute(
            "SELECT gist FROM events WHERE archived=0 AND superseded_by IS NULL "
            "AND subject IN ('self','shared') ORDER BY created_at DESC LIMIT ?",
            (int(limit),)) if (r["gist"] or "").strip()]
    except Exception:
        return []          # v11 旧库没有 subject 列


def _legacy_events(conn, user_name, limit=24):
    """兼容旧库：没有 self/shared 事件时，回退到人物关联的经历。"""
    from .persona import event_gists, find
    if not user_name:
        row = conn.execute(
            "SELECT p.canonical_name, COUNT(ed.source) c FROM personas p "
            "JOIN edges ed ON ed.target=p.id AND ed.type='involves' "
            "GROUP BY p.id ORDER BY c DESC LIMIT 1").fetchone()
        user_name = row["canonical_name"] if row else None
    p = find(conn, user_name) if user_name else None
    if not p:
        return []
    return [g for g in event_gists(conn, p["id"])[:limit] if (g or "").strip()]


def build(conn, user_name=None, *, subject_scope=None, allow_fallback=True):
    """生成自述。返回文本或 None；落库时带 scope 供上层判断是否可作人格。"""
    _ensure(conn)
    gists = _self_events(conn)
    scope = "self"
    if not gists and subject_scope != "self" and allow_fallback:
        gists, scope = _legacy_events(conn, user_name), "counterpart"
    if not gists:
        return None
    try:
        who = (identity.get(conn) or {}).get("profile", {}).get("who") or "陪伴者"
    except Exception:
        who = "陪伴者"
    label, rule = _SCOPE[scope]
    prompt = (_PROMPT.replace("{who}", who)
              .replace("{scope_label}", label)
              .replace("{scope_rule}", rule)
              .replace("{events}", "\n".join("- " + g for g in gists)))
    try:
        text = llm.generate(prompt, model=config.ANSWER_MODEL,
                            max_tokens=400, timeout=90).strip()
    except Exception:
        return None
    if not text:
        return None
    conn.execute(
        "INSERT OR REPLACE INTO self_narrative(id,text,updated_at,scope) "
        "VALUES(1,?,?,?)", (text, _now(), scope))
    conn.commit()
    return text


def get(conn):
    _ensure(conn)
    r = conn.execute(
        "SELECT text,updated_at,COALESCE(scope,'counterpart') AS scope "
        "FROM self_narrative WHERE id=1").fetchone()
    return dict(r) if r else None
