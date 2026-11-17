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
import re
from datetime import datetime, timezone

from . import config, identity, llm, settings

# v22.1：与画像同款强 JSON 契约。旧版裸 prompt + 400 token 会让 sensenova-flash
# 把"用户希望我…接下来分析经历…"整段思考写进 content（实测 581 字里思考占 456 字），
# 且它只有 </think> 没有 <think>，成对的剥离逻辑也接不住。JSON 契约实测 87 token
# 就返回干净自述。注意 prompt 里不要出现"思考过程"字样——那会诱导模型写分析。
_PROMPT = """你是自我叙事生成器。严格按下面的 JSON 输出，不要输出任何其他文字、分析或思考过程。
输出格式：{"text":"100字内的简体中文第一人称自述"}
固定身份（不可更改）：{who}
根据下面关于「{scope_label}」的经历写自述。
{scope_rule}
不要编造经历里没有的内容；字段 text 的值就是自述正文。
经历：
{events}"""

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


# 思考泄漏的标志：用户输入复述、分析编号、残留闭合标签。命中即拒，
# 绝不能让它落库后被 Brain 当成人格注入（一次污染会反复发作）。
_LEAK = re.compile(
    r"</?(think|thinking|reasoning|analysis|thought)>|"
    r"用户希望我|接下来分析|首先.{0,8}确认|然后整合|最后检查|"
    r"^\s*\d+[\.\)]\s+\"|注意语言要",
    re.I | re.M,
)


def _valid_narrative(text):
    t = (text or "").strip()
    if not t or len(set(t)) <= 2:
        return False
    if _LEAK.search(t):
        return False
    if len(t) >= 12:   # 够长才查语言占比；短文本（测试 mock / 占位）放行
        cn = len(re.findall(r"[\u4e00-\u9fff]", t))
        if cn < 12 or cn * 2 < len(t):
            return False
    return True


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
    # 主模型 + JSON 契约；仍退化则换 lite 模型重试一次（与画像同策略）
    lite = None
    try:
        lite = (settings.current().get("llm") or {}).get("extract_model")
    except Exception:
        pass
    attempts = [(config.ANSWER_MODEL, 600)]
    if lite and lite != config.ANSWER_MODEL:
        attempts.append((lite, 400))

    text = None
    for model, budget in attempts:
        try:
            raw = llm.generate(prompt, model=model, json_mode=True,
                               max_tokens=budget, timeout=90)
            s = str(raw or "").strip()
            try:
                import json as _json
                obj = _json.loads(s)
                got = str(obj.get("text") or "").strip() if isinstance(obj, dict) else s
            except (ValueError, TypeError):
                got = s          # 兼容非 JSON 的纯文本（测试 mock / 老端点）
        except Exception:
            continue
        if _valid_narrative(got):
            text = got
            break
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
