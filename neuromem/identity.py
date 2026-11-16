"""L0 固定身份层（宪法层）：陪伴 agent 的「我是谁 / 我服务谁」。

与 L1 持久化层严格分开：L0 是**内容不变的人工设定**，L1 是**只增不删的经历**。
把两者都叫「固化」会让实现混乱；旧系统把它们混在 self_narrative 里，由 LLM
从经历生成自传，于是出现了第三人称旧人设与 "Thinking Process" 噪声。

硬约束
------
- 人工版本化写入：只有 ``set_profile(actor=...)`` 且 actor 属于人工白名单才允许。
  拒绝 llm / agent / auto / evolve / drift / selfnarrative / reconsolidation 等来源。
- 运行时只读：get / block / verify / history 都不改内容（仅幂等建默认行）。
- 不 import llm，不参与遗忘 / 归档 / 再巩固 / 自叙事 / 漂移。
- 清库豁免：api.RESET_KEEP 含 identity / identity_history；导出含这两张表。
- 只增不删：每次升级先把旧版整份存入 identity_history，checksum 可校验未被篡改。

与 subject 的关系：L0 规定「谁是我」，L1 用 ``events.subject`` 标注每条记忆
属于谁（self / counterpart / shared / third_party）。L0 不写 L1，L1 不许写 L0。
"""
import hashlib
import json
from datetime import datetime, timezone

from . import config

PROFILE_FIELDS = ("who", "serve", "address", "tone", "limits")
FIELD_LABELS = {
    "who": "我是谁",
    "serve": "我服务谁",
    "address": "称呼与边界",
    "tone": "语气与禁忌",
    "limits": "能力边界",
}
MAX_FIELD_LEN = 200

# 默认模板是「待人工确认」的中性骨架，不替用户决定名字与关系。
DEFAULT_PROFILE = {
    "who": "一个陪你聊天、记得住事的陪伴者（名字待人工设定）",
    "serve": "屏幕对面这个人（称呼待人工设定）",
    "address": "用对方喜欢的方式称呼；不替对方做决定；不假装记得没发生过的事",
    "tone": "温和、简短，先接住情绪再谈事；不把推测当事实",
    "limits": "记忆层只给候选，是否说出口由我自己判断；检索不到就如实说不知道",
}

HUMAN_ACTORS = frozenset({"human", "admin", "cli", "api"})
FORBIDDEN_ACTORS = frozenset({
    "llm", "agent", "auto", "evolve", "drift", "selfnarrative",
    "reconsolidation", "consolidator", "prompt", "model",
})

_DDL = [
    """CREATE TABLE IF NOT EXISTS identity (
         id INTEGER PRIMARY KEY CHECK(id=1),
         version INTEGER NOT NULL DEFAULT 1,
         profile_json TEXT NOT NULL,
         checksum TEXT NOT NULL,
         updated_at TEXT,
         updated_by TEXT DEFAULT 'human')""",
    """CREATE TABLE IF NOT EXISTS identity_history (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         version INTEGER,
         profile_json TEXT,
         checksum TEXT,
         updated_at TEXT,
         updated_by TEXT,
         reason TEXT)""",
]


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _dump(profile):
    return json.dumps(profile, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def checksum(profile):
    """对规范化后的 profile 取短哈希，用于发现绕过 set_profile 的手工改库。"""
    return hashlib.sha256(_dump(profile).encode("utf-8")).hexdigest()[:16]


def _ensure(conn):
    """幂等建表 + 惰性写入默认模板；不改变已有身份。

    逐条 execute 而非 executescript：后者会隐式 commit，可能截断调用方事务。
    """
    for ddl in _DDL:
        conn.execute(ddl)
    row = conn.execute("SELECT 1 FROM identity WHERE id=1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO identity(id,version,profile_json,checksum,updated_at,updated_by) "
            "VALUES(1,1,?,?,?,'human')",
            (_dump(DEFAULT_PROFILE), checksum(DEFAULT_PROFILE), _now()))
        conn.commit()
    return conn


def get(conn):
    """返回当前固定身份（dict）；只读路径，仅首次会惰性建默认行。"""
    _ensure(conn)
    row = conn.execute(
        "SELECT version,profile_json,checksum,updated_at,updated_by "
        "FROM identity WHERE id=1").fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        d["profile"] = json.loads(d.pop("profile_json"))
    except Exception:
        d["profile"] = dict(DEFAULT_PROFILE)
    return d


def block(conn):
    """每轮注入用的紧凑文本。宪法全文不进每次请求，这里只给骨架（≤ 约 200 字）。"""
    cur = get(conn)
    if not cur:
        return ""
    p = cur["profile"]
    lines = ["【固定身份 · v%s（人工设定，不可被对话改写）】" % cur["version"]]
    for f in PROFILE_FIELDS:
        v = str(p.get(f) or "").strip()
        if v:
            lines.append("%s：%s" % (FIELD_LABELS[f], v[:80]))
    return "\n".join(lines)


def verify(conn):
    """校验字段完整、类型合法、checksum 一致、版本单调。返回 (ok, msg)。"""
    cur = get(conn)
    if not cur:
        return False, "固定身份不存在"
    p = cur["profile"]
    if not isinstance(p, dict) or set(p) != set(PROFILE_FIELDS):
        return False, "固定身份字段不完整：%s" % ",".join(sorted(set(PROFILE_FIELDS) - set(p)))
    for f in PROFILE_FIELDS:
        if not isinstance(p[f], str) or len(p[f]) > MAX_FIELD_LEN:
            return False, "字段 %s 非法" % f
    if cur["checksum"] != checksum(p):
        return False, "checksum 不一致：疑似绕过人工版本化直接改库"
    rows = conn.execute(
        "SELECT version FROM identity_history ORDER BY id").fetchall()
    vs = [int(r["version"]) for r in rows] + [int(cur["version"])]
    if any(b <= a for a, b in zip(vs, vs[1:])):
        return False, "版本号未单调递增"
    return True, "ok"


def set_profile(conn, changes, *, actor="human", reason=""):
    """人工版本化升级固定身份。这是**唯一**允许的写入口。

    ``changes`` 只需给要改的字段；未给的沿用旧值。actor 必须属于人工白名单，
    LLM / 自进化 / 再巩固等来源一律拒绝。旧版本先整份归档再覆盖。
    """
    a = str(actor or "").strip().lower()
    if a not in HUMAN_ACTORS or a in FORBIDDEN_ACTORS:
        return {"ok": False, "error": "固定身份只能人工版本化升级，actor=%r 被拒绝" % actor}
    if not isinstance(changes, dict) or not changes:
        return {"ok": False, "error": "changes 必须是非空 dict"}
    bad = set(changes) - set(PROFILE_FIELDS)
    if bad:
        return {"ok": False, "error": "未知字段：%s" % "、".join(sorted(bad))}
    _ensure(conn)
    cur = get(conn)
    if not cur:
        return {"ok": False, "error": "固定身份读取失败"}
    new = dict(cur["profile"])
    for f, v in changes.items():
        if not isinstance(v, str) or not v.strip() or len(v) > MAX_FIELD_LEN:
            return {"ok": False, "error": "字段 %s 必须是 1~%d 字非空文本" % (f, MAX_FIELD_LEN)}
        new[f] = v.strip()
    version = int(cur["version"]) + 1
    stamp = _now()
    conn.execute(
        "INSERT INTO identity_history(version,profile_json,checksum,updated_at,"
        "updated_by,reason) VALUES(?,?,?,?,?,?)",
        (cur["version"], _dump(cur["profile"]), cur["checksum"], stamp, a,
         "升级前归档" if not reason else str(reason)[:200]))
    conn.execute(
        "UPDATE identity SET version=?,profile_json=?,checksum=?,updated_at=?,"
        "updated_by=? WHERE id=1",
        (version, _dump(new), checksum(new), stamp, a))
    conn.commit()
    return {"ok": True, "version": version, "checksum": checksum(new)}


def history(conn, limit=10):
    """历史版本（只读），用于审计「身份有没有被悄悄改过」。"""
    _ensure(conn)
    rows = conn.execute(
        "SELECT version,checksum,updated_at,updated_by,reason "
        "FROM identity_history ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def label_for(subject):
    """subject 的中文标签，供 MCP 输出标注「这条记忆关于谁」。"""
    return config.SUBJECT_LABELS.get(subject, "对方")


def normalize_subject(value):
    """校验并归一 subject；非法值回落到默认，避免脏值进 events。"""
    s = str(value or "").strip().lower()
    return s if s in config.SUBJECTS else config.DEFAULT_SUBJECT


# ---------------- v13 主体翻转：把「我」还给 agent ----------------
def subject_of(speaker, subject=None):
    """由「谁说的 + 显式 subject」推断记忆归属。

    speaker 只表示话是谁说的，**不再**表示记忆属于谁。agent 侧发言默认
    记到 self（我的承诺 / 反思），对方发言默认记到 counterpart。
    """
    if subject is not None:
        return normalize_subject(subject)
    if getattr(config, "SUBJECT_VIEW", False) and \
            str(speaker or "").strip().lower() in config.AGENT_SPEAKERS:
        return "self"
    return config.DEFAULT_SUBJECT


def first_person_target(speaker, subject):
    """gist 里第一人称「我」应该落到的名字。

    旧系统无条件把「我」替换成「用户」，于是 agent 说「我答应过每周都来」
    会被写成「用户答应过每周都来」——这正是主体错位的根源。
    - counterpart → 对话对象名（默认仍是「用户」，保证旧库/旧单测兼容）
    - self/shared → SELF_NAME（等于保留原话，归位到 agent 自己）
    - third_party → None（不替换，忠实原文）
    """
    if not getattr(config, "SUBJECT_VIEW", False):
        return str(speaker or "") or None
    s = normalize_subject(subject)
    if s == "counterpart":
        return str(speaker or "").strip() or config.COUNTERPART_NAME
    if s in ("self", "shared"):
        return config.SELF_NAME
    return None


def perspective_hint(subject):
    """给生成层的一句主体提示，避免把「关于我的记忆」说成对方的事。"""
    return {
        "self": "这条是关于我（陪伴者自己）的记忆，回答里用「我」。",
        "counterpart": "这条是关于对方的记忆，回答里用「你」或对方名字。",
        "shared": "这条是我们共同经历的，回答里用「我们」。",
        "third_party": "这条是关于第三方的，不要说成「你」或「我」。",
    }.get(normalize_subject(subject), "")
