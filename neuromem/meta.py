"""元架构 + 阶段解锁：按发育指标判断当前阶段与已解锁能力。"""

STAGES = [
    ("感知期", 0, "能感知、记录事件"),
    ("情节期", 1, "能回忆具体事件"),
    ("概念期", 2, "能从经历归纳概念"),
    ("社会期", 3, "有人物与关系"),
    ("自我期", 4, "能形成更高层概念(L2)"),
    ("成熟期", 5, "经历与概念充足"),
]


def metrics(conn):
    def q(sql):
        return conn.execute(sql).fetchone()[0]

    return {
        "events": q("SELECT COUNT(*) FROM events WHERE archived=0"),
        "concepts": q("SELECT COUNT(*) FROM concepts"),
        "max_level": q("SELECT COALESCE(MAX(abstraction_level),0) FROM concepts"),
        "personas": q("SELECT COUNT(*) FROM personas"),
        "relations": q("SELECT COUNT(*) FROM relations"),
    }


def _satisfied(name, m):
    if name == "感知期":
        return m["events"] >= 1
    if name == "情节期":
        return m["events"] >= 3
    if name == "概念期":
        return m["concepts"] >= 1
    if name == "社会期":
        return m["personas"] >= 2 or m["relations"] >= 1
    if name == "自我期":
        return m["max_level"] >= 2
    if name == "成熟期":
        return m["events"] >= 20 and m["concepts"] >= 5
    return False


_STAGE_NEED = {
    "感知期": ("events", 1, "条事件", ""),
    "情节期": ("events", 3, "条事件", ""),
    "概念期": ("concepts", 1, "个概念", ""),
    "社会期": ("personas", 2, "个人物", "或关系 ≥1"),
    "自我期": ("max_level", 2, "层概念", "L2 抽象"),
    "成熟期": ("events", 20, "条事件", "且概念 ≥5"),
}


def report(conn):
    m = metrics(conn)
    unlocked = []
    level = -1
    broken = False
    stages = []
    for name, lvl, desc in STAGES:
        met = _satisfied(name, m)
        reachable = met and not broken   # 阶段必须连续解锁
        key, need, unit, note = _STAGE_NEED[name]
        stages.append({
            "name": name, "level": lvl, "desc": desc,
            "satisfied": reachable, "met": met,
            "have": m.get(key, 0), "need": need, "unit": unit, "note": note,
        })
        if reachable:
            unlocked.append(name)
            level = lvl
        else:
            broken = True
    return {
        "stage": unlocked[-1] if unlocked else "空白",
        "level": level,
        "unlocked": unlocked,
        "metrics": m,
        "stages": stages,
    }