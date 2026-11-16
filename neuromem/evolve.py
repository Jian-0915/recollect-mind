"""自进化：一轮代谢 = 巩固 + 概念再抽象 + 遗忘 + 成长快照。"""
from datetime import datetime, timezone

from . import alias, config, consolidator, db, forgetting, store


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def snapshot(conn):
    events = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    active = conn.execute("SELECT COUNT(*) c FROM events WHERE archived=0").fetchone()["c"]
    archived = conn.execute("SELECT COUNT(*) c FROM events WHERE archived=1").fetchone()["c"]
    concepts = conn.execute("SELECT COUNT(*) c FROM concepts").fetchone()["c"]
    max_level = conn.execute(
        "SELECT COALESCE(MAX(abstraction_level),0) m FROM concepts"
    ).fetchone()["m"]
    ts = _now()
    conn.execute(
        "INSERT INTO growth_log(ts,events,active_events,concepts,max_level,archived) "
        "VALUES(?,?,?,?,?,?)",
        (ts, events, active, concepts, max_level, archived),
    )
    conn.commit()
    return {
        "ts": ts,
        "events": events,
        "active": active,
        "concepts": concepts,
        "max_level": max_level,
        "archived": archived,
    }


def tick(conn):
    """跑一轮自进化代谢，返回本轮的产出与成长快照。"""
    new_concepts = consolidator.run(conn)
    abstracted = consolidator.run_concepts(conn)
    decay = forgetting.decay(conn)

    # 情感随代谢回落（失败静默）
    try:
        from . import emotion
        emotion.EmotionState(conn).decay()
    except Exception:
        pass

    # 随机表征漂移默认关闭：它与「被采用才定向重编码」的再巩固冲突，
    # 会让没被想起的记忆莫名其妙改变语义。保留实现供对照实验使用。
    drift_res = None
    if config.DRIFT_ENABLED:
        try:
            from . import drift
            drift_res = drift.drift(conn)
        except Exception:
            drift_res = None

    snap = snapshot(conn)

    # 人物画像：自动补齐 / 刷新（每轮最多 2 个，失败留给下一轮）
    profiles = []
    try:
        from . import persona
        profiles = persona.build_profiles(conn)
    except Exception:
        profiles = []

    # 更正链补建（C1）：历史漏判的互斥说法在这里收口
    conflicts = 0
    try:
        conflicts = store.rebuild_conflicts(conn)
    except Exception:
        conflicts = 0

    # 实体别名兜底：导入 / 编辑 / 老数据漏学的在这里补（规则匹配，幂等，成本很低）
    learned = 0
    try:
        learned = alias.backfill(conn)
    except Exception:
        learned = 0

    # 每 3 轮刷新一次自我叙事（额外一次 LLM 调用，不每轮都跑）
    narrative = None
    try:
        n = conn.execute("SELECT COUNT(*) FROM growth_log").fetchone()[0]
        if n and n % 3 == 0:
            from . import selfnarrative
            narrative = selfnarrative.build(conn, None)
    except Exception:
        narrative = None

    # 每轮代谢后把 WAL 合并回主库（避免 WAL 长期膨胀）
    db.checkpoint(conn)

    return {
        "new_concepts": new_concepts,
        "abstracted": abstracted,
        "decay": decay,
        "drift": drift_res,
        "snapshot": snap,
        "narrative": narrative,
        "profiles": profiles,
        "conflicts": conflicts,
        "aliases": learned,
    }