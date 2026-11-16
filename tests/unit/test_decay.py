"""时间锚点遗忘：由真实 Δt 与连续 τ 决定，与代谢轮数无关。

旧版断言基于「跑 N 轮」；2026-11-14 改为 exp(-Δt/τ) 后，用例改为
直接快进时间，并显式覆盖「服务停摆后重启要一次补算」这个核心回归。
"""
import json
from datetime import datetime, timedelta, timezone

from neuromem import config, forgetting


def _now():
    return datetime.now(timezone.utc).astimezone()


def _mk(conn, eid, salience, created=None, recalled=None, count=0,
        affect=None, surprise=0.5, activation=1.0):
    created = created or _now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO events(id,created_at,gist,activation,salience,archived,"
        "last_recalled_at,recall_count,affect_json,surprise,confidence) "
        "VALUES(?,?,?,?,?,0,?,?,?,?,0.9)",
        (eid, created, "事件" + eid, activation, salience, recalled, count,
         json.dumps(affect or {}, ensure_ascii=False), surprise))


def _act(conn, eid):
    r = conn.execute("SELECT activation,archived FROM events WHERE id=?", (eid,)).fetchone()
    return float(r["activation"]), int(r["archived"])


def test_trivial_fades_before_important(memdb, conn):
    now = _now()
    _mk(conn, "trivial", 0.02, surprise=0.05)
    _mk(conn, "important", 0.85, affect={"valence": -0.8, "arousal": 0.8})
    conn.commit()
    forgetting.decay(conn, now=now + timedelta(days=60))
    assert _act(conn, "trivial")[1] == 1, "琐事 60 天后应已归档"
    assert _act(conn, "important")[1] == 0 and _act(conn, "important")[0] > 0.3


def test_repeated_calls_are_idempotent(memdb, conn):
    now = _now()
    _mk(conn, "e1", 0.3)
    conn.commit()
    forgetting.decay(conn, now=now + timedelta(days=10))
    a = _act(conn, "e1")
    for _ in range(300):
        forgetting.decay(conn, now=now + timedelta(days=10))
    b = _act(conn, "e1")
    assert abs(a[0] - b[0]) < 1e-9, "同一时刻重复代谢不该重复衰减"
    forgetting.decay(conn, now=now + timedelta(days=40))
    assert _act(conn, "e1")[0] < a[0] * 0.8, "时间继续流逝应继续衰减"


def test_downtime_is_caught_up(memdb, conn):
    """服务停 2 天再启动跑一次 vs 常驻每 10 分钟一轮，结果必须一致。"""
    now = _now()
    _mk(conn, "e1", 0.25)
    conn.commit()
    for i in range(288):                      # 288 轮 × 10 分钟 = 2 天
        forgetting.decay(conn, now=now + timedelta(minutes=10 * (i + 1)))
    online = _act(conn, "e1")[0]
    # 同一条记忆恢复到完全相同的初始状态，只改「代谢方式」
    conn.execute(
        "UPDATE events SET activation=1.0,archived=0,last_decay_at=NULL,"
        "last_recalled_at=NULL WHERE id='e1'")
    conn.commit()
    forgetting.decay(conn, now=now + timedelta(days=2))
    offline = _act(conn, "e1")[0]
    assert abs(online - offline) < 0.02, (
        "停摆补偿与常驻代谢不一致：%.4f vs %.4f" % (online, offline))


def test_recalled_memory_survives_longer(memdb, conn):
    now = _now()
    recent = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    _mk(conn, "never", 0.10, count=0)
    _mk(conn, "used", 0.10, recalled=recent, count=6)
    conn.commit()
    forgetting.decay(conn, now=now + timedelta(days=90))
    assert _act(conn, "used")[0] > _act(conn, "never")[0], "被反复采用的记忆应更牢"


def test_oldest_memory_is_not_immune(memdb, conn):
    """旧版把「库里第一条」当锚点永久保护，越老越不忘 —— 这里必须能忘。"""
    now = _now()
    old = (now - timedelta(days=3650)).isoformat(timespec="seconds")
    _mk(conn, "oldest", 0.02, created=old, surprise=0.05)
    conn.commit()
    forgetting.decay(conn, now=now + timedelta(days=90))
    assert _act(conn, "oldest")[1] == 1


def test_tau_is_continuous_and_monotonic(memdb, conn):
    """τ 不能被压缩成三档阶梯。"""
    now = _now()
    taus = [forgetting._tau_days({"salience": s / 100.0, "confidence": 0.9,
                                  "surprise": 0.5, "affect_json": "{}",
                                  "recall_count": 0}, False, now)
            for s in range(0, 101)]
    assert len({round(t, 4) for t in taus}) > 50, "τ 应连续，不是分档"
    assert taus[0] < taus[-1] and all(
        taus[i] <= taus[i + 1] + 1e-9 for i in range(len(taus) - 1))


def test_reinforce_increments_count(memdb, conn):
    _mk(conn, "e1", 0.3, activation=0.9)
    conn.commit()
    forgetting.reinforce(conn, "e1", 0.1)
    conn.commit()
    row = conn.execute(
        "SELECT activation,recall_count,last_decay_at FROM events WHERE id='e1'"
    ).fetchone()
    assert row["recall_count"] == 1
    assert row["activation"] >= 0.99
    assert row["last_decay_at"], "被想起应重置计龄时钟"


def test_tier_params_sane():
    """旧常量保留兼容，但已不参与 decay。"""
    assert 0.99 < config.DECAY_TRIVIAL < 0.999
    assert config.DECAY_NORMAL > config.DECAY_TRIVIAL
    assert config.DECAY_IMPORTANT > config.DECAY_NORMAL
    assert config.DECAY_ANCHOR > config.DECAY_IMPORTANT
    assert config.TAU_MIN_DAYS < config.TAU_MAX_DAYS
