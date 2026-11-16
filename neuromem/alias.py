"""实体别名：把类别词与具体名字连起来（"猫" ↔ "团子"）。

问题背景：记忆里是「团子特别怕吹风机」和「养了一只猫，名字叫团子」，
但"猫"与"团子"没有任何关联，问"我的猫怕什么"就召回不到。
这里从原话里学「类别 → 名字」，写入时顺手登记，查询时把类别扩展成名字。
"""
import re

KINDS = ("猫", "猫咪", "狗", "狗狗", "宠物", "兔子", "仓鼠", "鹦鹉", "乌龟", "鱼")

# "养了一只猫，名字叫团子" / "猫叫团子" / "猫咪取名团子"
_LEARN = re.compile(
    r"(猫|猫咪|狗|狗狗|宠物|兔子|仓鼠|鹦鹉|乌龟)[^。；]{0,16}?"
    r"(?:名字叫|名叫|叫做|取名|叫)\s*([\u4e00-\u9fa5A-Za-z]{1,6})")

# "团子是一只猫" / "团子是只猫" / "团子属于猫科"
_REVERSE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z]{1,6})\s*(?:是一只|是只|是|属于)\s*(猫|狗|宠物|兔子|仓鼠|鹦鹉|乌龟)")

# "团子（猫）" / "团子(一只猫)"
_BRACKET = re.compile(
    r"([\u4e00-\u9fa5A-Za-z]{1,6})\s*[（(]\s*(?:一只|只)?\s*(猫|狗|宠物|兔子|仓鼠|鹦鹉|乌龟)\s*[)）]")


def learn(conn, text):
    """从一句话里学「类别 → 名字」，返回新登记的 (alias, target)。"""
    t = text or ""
    out = []
    for m in _LEARN.finditer(t):
        kind, name = m.group(1), m.group(2)
        if name in KINDS or name == kind:
            continue
        if _add(conn, kind, name):
            out.append((kind, name))
    for m in _REVERSE.finditer(t):
        name, kind = m.group(1), m.group(2)
        if name in KINDS or name == kind:
            continue
        if _add(conn, kind, name):
            out.append((kind, name))
    for m in _BRACKET.finditer(t):
        name, kind = m.group(1), m.group(2)
        if name in KINDS or name == kind:
            continue
        if _add(conn, kind, name):
            out.append((kind, name))
    return out


def _add(conn, alias, target):
    cur = conn.execute(
        "INSERT OR IGNORE INTO entity_alias(alias,target,kind) VALUES(?,?, 'category')",
        (alias, target))
    return bool(cur.rowcount)


def add(conn, alias_text, target, kind="category"):
    """手动登记（公开接口）：用户明确指定「类别 → 名字」。"""
    a = (alias_text or "").strip()[:12]
    t = (target or "").strip()[:12]
    if not a or not t or a == t:
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO entity_alias(alias,target,kind) VALUES(?,?,?)",
        (a, t, kind or "category"))
    conn.commit()
    return bool(cur.rowcount)


def remove(conn, alias_text, target):
    cur = conn.execute("DELETE FROM entity_alias WHERE alias=? AND target=?",
                       ((alias_text or "").strip(), (target or "").strip()))
    conn.commit()
    return bool(cur.rowcount)


def targets(conn, query):
    """查询里出现了哪些类别词 → 返回它们指向的名字。"""
    if not query:
        return []
    out = []
    for r in conn.execute("SELECT alias,target FROM entity_alias"):
        if r["alias"] and r["alias"] in query and r["target"] not in out:
            out.append(r["target"])
    return out


def all_aliases(conn):
    return [{"alias": r["alias"], "target": r["target"], "kind": r["kind"]}
            for r in conn.execute("SELECT alias,target,kind FROM entity_alias ORDER BY alias")]


def backfill(conn):
    """给历史记忆补学别名（幂等）。"""
    n = 0
    for r in conn.execute("SELECT id,gist FROM events WHERE archived=0"):
        if learn(conn, r["gist"]):
            n += 1
    conn.commit()
    return n