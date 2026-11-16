"""时间锚点遗忘：记忆强度是「真实经过的时间」的函数，与代谢轮数无关。

2026-11-14 的修正
------------------
旧版 ``decay()`` 每轮把 activation 乘一个固定系数（琐事 0.9934 / 普通
0.99971 / ...），于是「同样过去 30 天」的结果取决于服务跑了多久：实测
服务常驻 4320 轮 → 均值 0.387 / 归档 148，只开过 1 小时 6 轮 → 均值
0.810 / 归档 0。而且 SQL 里根本没有 created_at，「最老的一条」还被当成
锚点永久保护 —— 越老越不忘。

新版：

    act *= exp(-Δt / τ)

- Δt 取 ``max(last_decay_at, last_recalled_at, created_at)`` 到现在的真实
  时间；服务停摆再启动会一次补算，重复调用幂等。
- τ 连续（不是三档阶梯）：由 salience / 情绪强度 / surprise / 置信度 /
  人物关联 / 复习次数共同决定；被想起一次就把计龄时钟重置。
- 归档仍只是 ``archived=1``，数据不删除，可随时恢复。

``_factor`` 等旧接口保留，仅为兼容外部脚本，不再参与计算。
"""
import json
import math
from datetime import datetime, timedelta, timezone

from . import config

RECALL_PROTECT_DAYS = 30
_DAY = 86400.0


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _parse(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt


def _recent(ts, now, days):
    dt = _parse(ts)
    return bool(dt and (now - dt) < timedelta(days=days))


def _clamp(v, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return lo


def _factor(row, anchor_id, now, has_persona):
    """旧版分档系数：仅为兼容外部脚本保留，decay 已不再使用。"""
    if row["id"] == anchor_id:
        return config.DECAY_ANCHOR
    if _recent(row["last_recalled_at"], now, RECALL_PROTECT_DAYS):
        return config.DECAY_RECALLED
    sal = float(row["salience"] or 0.0)
    count = int(row["recall_count"] or 0)
    if sal >= 0.6 or count >= 3:
        return config.DECAY_IMPORTANT
    if sal < 0.1 and count == 0 and not has_persona:
        return config.DECAY_TRIVIAL
    try:
        if float(row["confidence"] or 1.0) < 0.6 and count == 0:
            return config.DECAY_TRIVIAL
    except (TypeError, ValueError):
        pass
    return config.DECAY_NORMAL


def _tau_days(d, has_persona, now):
    """连续半衰期尺度（天）：越显著 / 越有情绪 / 越意外 / 越常用 → 越难忘。"""
    sal = _clamp(d.get("salience") or 0.0)
    conf = _clamp(d.get("confidence") if d.get("confidence") is not None else 0.9)
    surprise = _clamp(d.get("surprise") if d.get("surprise") is not None else 0.5)
    try:
        aff = json.loads(d.get("affect_json") or "{}")
    except (ValueError, TypeError):
        aff = {}
    emo = (0.5 * abs(_clamp(aff.get("valence") or 0.0, -1.0, 1.0))
           + 0.5 * _clamp(aff.get("arousal") or 0.0))
    importance = (0.50 * sal + 0.20 * emo + 0.12 * surprise
                  + 0.08 * conf + (0.10 if has_persona else 0.0))
    if conf < 0.65:                      # 「听说 / 好像」的可信度低，淡得更快
        importance *= 0.75
    importance = _clamp(importance)
    tau = (config.TAU_MIN_DAYS
           + (config.TAU_MAX_DAYS - config.TAU_MIN_DAYS)
           * importance ** config.TAU_CURVE)
    cnt = int(d.get("recall_count") or 0)
    tau *= 1.0 + config.TAU_REVIEW_GAIN * math.log1p(cnt)
    if _recent(d.get("last_recalled_at"), now, RECALL_PROTECT_DAYS):
        tau *= 1.25                      # 近 30 天被想起：额外保护
    return max(config.TAU_MIN_DAYS, tau)


def decay(conn, now=None, threshold=None):
    """按真实 Δt 结算一次衰减，返回 {"total", "newly_archived", "elapsed_days"}。

    ``now`` 可传入 datetime 用于快进验证；同一时刻重复调用不会重复衰减。
    """
    threshold = config.DECAY_THRESHOLD if threshold is None else threshold
    now = now or datetime.now(timezone.utc).astimezone()
    stamp = now.isoformat(timespec="seconds")
    linked = {r[0] for r in conn.execute(
        "SELECT e.source FROM edges e JOIN personas p ON e.target=p.id "
        "WHERE e.type='involves' AND p.canonical_name<>'用户'")}
    rows = conn.execute("SELECT * FROM events WHERE archived=0").fetchall()
    updates, newly, max_dt = [], 0, 0.0
    for r in rows:
        d = dict(r)
        act = float(d.get("activation") if d.get("activation") is not None else 1.0)
        anchors = [_parse(d.get(k)) for k in
                   ("last_decay_at", "last_recalled_at", "created_at")]
        anchors = [t for t in anchors if t is not None and t <= now]
        last = max(anchors) if anchors else now
        dt_days = max(0.0, (now - last).total_seconds()) / _DAY
        max_dt = max(max_dt, dt_days)
        if dt_days <= 0:
            continue
        tau = _tau_days(d, d["id"] in linked, now)
        new_act = act * math.exp(-dt_days / tau)
        new_arch = 1 if new_act < threshold else 0
        newly += new_arch
        updates.append((round(new_act, 6), new_arch, stamp, d["id"]))
    if updates:
        conn.executemany(
            "UPDATE events SET activation=?,archived=?,last_decay_at=? WHERE id=?",
            updates)
        conn.commit()
    return {"total": len(rows), "newly_archived": newly,
            "elapsed_days": round(max_dt, 4)}


def reinforce(conn, event_id, amount=0.1):
    """被想起 / 被采用时强化一条记忆，并把计龄时钟重置到此刻（不 commit）。"""
    row = conn.execute("SELECT activation FROM events WHERE id=?", (event_id,)).fetchone()
    if not row:
        return
    act = min(1.0, (row["activation"] or 0.0) + amount)
    now = _now()
    try:
        conn.execute(
            "UPDATE events SET activation=?, last_recalled_at=?, last_decay_at=?, "
            "recall_count=COALESCE(recall_count,0)+1 WHERE id=?",
            (round(act, 4), now, now, event_id))
    except Exception:                     # 旧库无 last_decay_at 列时仍可工作
        conn.execute(
            "UPDATE events SET activation=?, last_recalled_at=?, "
            "recall_count=COALESCE(recall_count,0)+1 WHERE id=?",
            (round(act, 4), now, event_id))
