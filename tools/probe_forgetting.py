"""跨天遗忘 / 巩固观测探针。

为什么需要它：遗忘（2/45/180 天分层衰减）和再巩固此前只用脚本把时间戳
**快进**验证过逻辑，真实日历时间一天都没走过。快进能证明公式自洽，不能
证明"每天实际结算一次"在真实作息下也对——比如 tick 没跑、时区算错、
结算被重复扣两次，这些只有真等才有结果。

用法：
    python tools/probe_forgetting.py --init      # 选样本，记基线
    python tools/probe_forgetting.py --sample    # 每次跑：记实测、对照理论、报异常

产物：
    data/forgetting_probe.json   探针定义 + 基线（只在 --init 时写）
    data/forgetting_probe.csv    每次采样追加一行（长期趋势看这个）

设计取舍：只读主库，不 decay、不 reinforce、不改任何数据 —— 观测不该扰动
被观测的东西。真正的 decay 由服务里的 autoevolve tick 去跑。
"""
import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import config, db, forgetting  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(BASE, "data", "forgetting_probe.json")
CSV_PATH = os.path.join(BASE, "data", "forgetting_probe.csv")
DAY = 86400.0
N_SAMPLES = 8


def _now():
    return datetime.now(timezone.utc).astimezone()


def _parse(ts):
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def pick_samples(conn):
    """按 salience 分位挑，覆盖高/中/低与被人物关联的两种。"""
    linked = {r[0] for r in conn.execute(
        "SELECT e.source FROM edges e JOIN personas p ON e.target=p.id "
        "WHERE e.type='involves' AND p.canonical_name<>'用户'")}
    rows = conn.execute(
        "SELECT id,gist,salience,affect_json,created_at,activation,recall_count,"
        "subject,superseded_by FROM events WHERE archived=0 AND superseded_by IS NULL "
        "ORDER BY salience DESC").fetchall()
    if not rows:
        return []
    picks, seen = [], set()
    # 高分位、低分位、中间各取，再补上"有情绪"和"有 persona 关联"各一条
    idxs = [0, len(rows) // 4, len(rows) // 2, 3 * len(rows) // 4, len(rows) - 1]
    for i in idxs:
        if 0 <= i < len(rows) and rows[i]["id"] not in seen:
            picks.append(rows[i])
            seen.add(rows[i]["id"])
    for r in rows:
        if len(picks) >= N_SAMPLES:
            break
        if r["id"] in seen:
            continue
        try:
            aff = json.loads(r["affect_json"] or "{}")
        except ValueError:
            aff = {}
        if abs(float(aff.get("valence") or 0)) > 0.4 and r["id"] not in seen:
            picks.append(r)
            seen.add(r["id"])
    for r in rows:
        if len(picks) >= N_SAMPLES:
            break
        if r["id"] in linked and r["id"] not in seen:
            picks.append(r)
            seen.add(r["id"])
    return picks[:N_SAMPLES]


def cmd_init(conn):
    picks = pick_samples(conn)
    if not picks:
        print("没有可采样的事件")
        return 1
    now = _now()
    samples = []
    for r in picks:
        d = dict(r)
        tau = forgetting._tau_days(d, False, now)
        samples.append({
            "id": d["id"], "gist": (d["gist"] or "")[:40],
            "salience": d["salience"], "subject": d.get("subject"),
            "baseline_activation": float(d.get("activation") or 1.0),
            # 基线时刻必须是「记录这一刻」，不能取 created_at：否则下次采样
            # 会把整条记忆的生命周期重复算一遍衰减，得出假偏差。
            "baseline_at": now.isoformat(timespec="seconds"),
            "created_at": d.get("created_at"),
            "tau_days": round(tau, 3),
            "recall_count": d.get("recall_count") or 0,
        })
    payload = {"created": now.isoformat(timespec="seconds"),
               "threshold": config.DECAY_THRESHOLD,
               "tau_range": [config.TAU_MIN_DAYS, config.TAU_MAX_DAYS],
               "samples": samples}
    os.makedirs(os.path.dirname(PROBE), exist_ok=True)
    with open(PROBE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("已写入探针 %d 条 -> %s" % (len(samples), PROBE))
    for s in samples:
        print("  %s sal=%.2f tau=%.1fd  %s"
              % (s["id"], s["salience"] or 0, s["tau_days"], s["gist"][:26]))
    return 0


def cmd_sample(conn):
    if not os.path.exists(PROBE):
        print("探针不存在，先跑 --init")
        return 1
    with open(PROBE, encoding="utf-8") as f:
        payload = json.load(f)
    now = _now()
    rows_out, alerts = [], []
    for s in payload["samples"]:
        r = conn.execute(
            "SELECT id,gist,activation,archived,last_decay_at,recall_count,"
            "revision_count,last_reconsolidated_at FROM events WHERE id=?",
            (s["id"],)).fetchone()
        if not r:
            alerts.append("%s 事件已不存在（被物理删除？）" % s["id"])
            continue
        base_at = _parse(s["baseline_at"]) or now
        days = max(0.0, (now - base_at).total_seconds()) / DAY
        tau = float(s["tau_days"] or config.TAU_MAX_DAYS)
        expected = float(s["baseline_activation"]) * math.exp(-days / tau)
        act = float(r["activation"] if r["activation"] is not None else 1.0)
        rec = {"ts": now.isoformat(timespec="seconds"),
               "days": round(days, 3), "id": s["id"], "gist": (r["gist"] or "")[:30],
               "salience": s["salience"], "tau": round(tau, 2),
               "activation": round(act, 4), "expected": round(expected, 4),
               "diff": round(act - expected, 4), "archived": r["archived"],
               "recalls": r["recall_count"], "revisions": r["revision_count"],
               "decayed_at": r["last_decay_at"] or ""}
        rows_out.append(rec)

        # 异常判据：误差超过 25% 或归档状态与理论不符
        if abs(rec["diff"]) > 0.25 * max(expected, 0.05):
            alerts.append("%s 实测 %.3f 与理论 %.3f 偏差 %.3f（tau=%.1fd, %.2fd）"
                          % (s["id"], act, expected, rec["diff"], tau, days))
        if r["archived"] and expected > config.DECAY_THRESHOLD:
            alerts.append("%s 被归档但理论值 %.3f 仍高于阈值 %.2f —— 可能衰减过快"
                          % (s["id"], expected, config.DECAY_THRESHOLD))
        if not r["archived"] and expected < config.DECAY_THRESHOLD * 0.5:
            alerts.append("%s 未归档但理论值 %.3f 远低于阈值 —— 衰减可能没在跑"
                          % (s["id"], expected))
        if act > float(s["baseline_activation"]) + 0.01 and r["recall_count"] == s["recall_count"]:
            alerts.append("%s activation 上升但 recall_count 未变 —— 谁改了它？" % s["id"])

    new_file = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()) if rows_out else ["ts"])
        if new_file:
            w.writeheader()
        for r in rows_out:
            w.writerow(r)

    print("%-18s %6s %6s %7s %7s %6s %6s"
          % ("id", "days", "tau", "act", "expect", "arch", "revis"))
    for r in rows_out:
        print("%-18s %6.2f %6.1f %7.3f %7.3f %6d %6d"
              % (r["id"][:18], r["days"], r["tau"], r["activation"],
                 r["expected"], r["archived"], r["revisions"]))
    print("\n记录追加到 %s（累计 %d 行）"
          % (CSV_PATH, sum(1 for _ in open(CSV_PATH, encoding="utf-8")) - 1))
    if alerts:
        print("\n异常 %d 条：" % len(alerts))
        for a in alerts:
            print("  !", a)
    else:
        print("\n无异常：实测与理论一致，归档状态符合预期。")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--sample", action="store_true")
    args = ap.parse_args()
    conn = db.connect()
    try:
        if args.init:
            return cmd_init(conn)
        return cmd_sample(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
