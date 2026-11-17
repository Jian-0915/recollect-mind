"""模型调用用量：次数 / Token / 延迟 / 失败，按天与按模型聚合。

热路径调用，使用独立长连接 + 进程内锁；任何异常都吞掉，绝不影响主流程。
"""
import sqlite3
import threading
from datetime import datetime, timedelta

from . import config

_lock = threading.RLock()   # 可重入：clear() 持锁时还会进 _db()
_conn = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  kind TEXT,
  role TEXT,
  provider TEXT,
  model TEXT,
  ok INTEGER,
  latency_ms INTEGER,
  prompt_tokens INTEGER DEFAULT 0,
  completion_tokens INTEGER DEFAULT 0,
  think_tokens INTEGER DEFAULT 0,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log(ts);
"""


def _migrate(c):
    """老库补列：CREATE TABLE IF NOT EXISTS 不会给已存在的表加字段。"""
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(usage_log)")}
        if "think_tokens" not in cols:
            c.execute("ALTER TABLE usage_log ADD COLUMN think_tokens INTEGER DEFAULT 0")
            c.commit()
    except Exception:
        pass


def _db():
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is None:
            c = sqlite3.connect(str(config.DB_PATH), check_same_thread=False, timeout=10)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA busy_timeout=5000")
            c.executescript(_SCHEMA)
            c.commit()
            _migrate(c)
            _conn = c
    return _conn


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def record(kind, role, provider, model, ok, latency_ms,
           prompt_tokens=0, completion_tokens=0, error="", think_tokens=0):
    """记录一次模型调用。失败静默，不影响业务。

    ``think_tokens`` 是这次真实消耗的思考 token：与正文共用 max_tokens 池子，
    单独记下来才能回答「是不是思考把输出挤掉了」。
    """
    try:
        with _lock:
            c = _db()
            c.execute(
                "INSERT INTO usage_log(ts,kind,role,provider,model,ok,latency_ms,"
                "prompt_tokens,completion_tokens,think_tokens,error) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), kind, role or "", provider or "", model or "",
                 1 if ok else 0, int(latency_ms or 0),
                 int(prompt_tokens or 0), int(completion_tokens or 0),
                 int(think_tokens or 0), (error or "")[:300]),
            )
            c.commit()
    except Exception:
        pass


def summary(days=7):
    """返回 {totals, by_day, by_model, recent}。"""
    days = max(1, min(int(days or 7), 90))
    since = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    c = _db()
    rows = c.execute("SELECT * FROM usage_log WHERE ts>=? ORDER BY id DESC", (since,)).fetchall()

    totals = {"calls": 0, "ok": 0, "failed": 0, "tokens": 0, "latency_sum": 0,
              "today_calls": 0, "today_tokens": 0}
    by_day, by_model = {}, {}
    for r in rows:
        toks = (r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0)
        totals["calls"] += 1
        totals["ok"] += 1 if r["ok"] else 0
        totals["failed"] += 0 if r["ok"] else 1
        totals["tokens"] += toks
        totals["latency_sum"] += r["latency_ms"] or 0
        day = (r["ts"] or "")[:10]
        if day == today:
            totals["today_calls"] += 1
            totals["today_tokens"] += toks

        d = by_day.setdefault(day, {"calls": 0, "failed": 0, "tokens": 0})
        d["calls"] += 1
        d["failed"] += 0 if r["ok"] else 1
        d["tokens"] += toks

        key = (r["kind"], r["model"])
        m = by_model.setdefault(key, {"kind": r["kind"], "model": r["model"], "calls": 0,
                                      "failed": 0, "tokens": 0, "latency_sum": 0})
        m["calls"] += 1
        m["failed"] += 0 if r["ok"] else 1
        m["tokens"] += toks
        m["latency_sum"] += r["latency_ms"] or 0

    day_series = []
    for i in range(days - 1, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        v = by_day.get(day, {"calls": 0, "failed": 0, "tokens": 0})
        day_series.append({"day": day[5:], "calls": v["calls"],
                           "failed": v["failed"], "tokens": v["tokens"]})

    models = []
    for m in by_model.values():
        calls = m["calls"] or 1
        models.append({"kind": m["kind"], "model": m["model"], "calls": m["calls"],
                       "failed": m["failed"], "tokens": m["tokens"],
                       "avg_ms": round(m["latency_sum"] / calls)})
    models.sort(key=lambda x: -x["calls"])

    recent = [{"ts": r["ts"], "kind": r["kind"], "role": r["role"], "model": r["model"],
               "ok": bool(r["ok"]), "ms": r["latency_ms"],
               "tokens": (r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0),
               "think": (r["think_tokens"] or 0),
               "error": r["error"]} for r in rows[:20]]

    calls = totals["calls"] or 1
    return {
        "days": days,
        "totals": {"calls": totals["calls"], "ok": totals["ok"], "failed": totals["failed"],
                   "tokens": totals["tokens"], "avg_ms": round(totals["latency_sum"] / calls),
                   "fail_rate": round(totals["failed"] / calls * 100, 1),
                   "today_calls": totals["today_calls"], "today_tokens": totals["today_tokens"]},
        "by_day": day_series,
        "by_model": models,
        "recent": recent,
    }


def clear(conn=None):
    """清用量日志。传入调用方的连接时不再碰模块级缓存连接。

    v13：api.reset_all 会传自己那条连接进来。模块级 ``_db()`` 缓存的是
    ``config.DB_PATH``，若调用方跑在副本库 / 临时库上，直接 ``_db()`` 会
    穿透去清主库的 usage_log —— 清库绝不该有这种旁路写入。
    """
    if conn is not None:
        conn.execute("DELETE FROM usage_log")
        conn.commit()
        return True
    with _lock:
        c = _db()
        c.execute("DELETE FROM usage_log")
        c.commit()
    return True