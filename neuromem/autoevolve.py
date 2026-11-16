"""自动进化：定时代谢 + 写入阈值触发 + 手动触发。

要点
- 进程内单线程调度 + 非阻塞锁：同一时刻只允许一轮代谢
- 活跃事件 < EVOLVE_MIN_EVENTS 直接跳过（省 LLM 调用）
- last_tick 以 growth_log 最新记录为准，跨进程可见
"""
import os
import threading
import time
from datetime import datetime, timedelta

from . import config, db, evolve

_lock = threading.Lock()
# 跨进程锁：MCP 与此网页服务同时跑时，只允许一个进程代谢（否则概念重复生成）
_LOCK_FILE = config.DATA_DIR / "evolve.lock"


def _acquire_file_lock():
    try:
        fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(str(_LOCK_FILE)) > 600:
                os.remove(str(_LOCK_FILE))  # 崩溃残留
        except OSError:
            pass
        return False
    except OSError:
        return False


def _release_file_lock():
    try:
        os.remove(str(_LOCK_FILE))
    except OSError:
        pass
_thread = None
_stop = threading.Event()
_state = {
    "running": False,
    "last_run": None,
    "last_result": None,
    "last_error": None,
    "runs": 0,
    "skipped": 0,
}


def enabled():
    flag = os.environ.get("NEUROMEM_AUTOEVOLVE", "1").strip().lower()
    return bool(config.AUTOEVOLVE) and flag not in ("0", "false", "off", "no")


def _now():
    return datetime.now().astimezone()


def _last_growth_ts():
    c = db.connect()
    try:
        row = c.execute("SELECT ts FROM growth_log ORDER BY id DESC LIMIT 1").fetchone()
        return row["ts"] if row else None
    finally:
        c.close()


def _active_events():
    c = db.connect()
    try:
        return c.execute("SELECT COUNT(*) FROM events WHERE archived=0").fetchone()[0]
    finally:
        c.close()


def events_since_last():
    """距上次代谢快照新增的活跃事件数。"""
    c = db.connect()
    try:
        row = c.execute("SELECT ts FROM growth_log ORDER BY id DESC LIMIT 1").fetchone()
        if not row or not row["ts"]:
            return c.execute("SELECT COUNT(*) FROM events WHERE archived=0").fetchone()[0]
        return c.execute(
            "SELECT COUNT(*) FROM events WHERE archived=0 AND created_at > ?",
            (row["ts"],)).fetchone()[0]
    finally:
        c.close()


def surprise_since_last():
    """距上次代谢快照新增的惊讶累积（预测误差总量）。"""
    c = db.connect()
    try:
        row = c.execute("SELECT ts FROM growth_log ORDER BY id DESC LIMIT 1").fetchone()
        if not row or not row["ts"]:
            return float(c.execute(
                "SELECT COALESCE(SUM(surprise),0) FROM events WHERE archived=0").fetchone()[0])
        return float(c.execute(
            "SELECT COALESCE(SUM(surprise),0) FROM events "
            "WHERE archived=0 AND created_at > ?", (row["ts"],)).fetchone()[0])
    finally:
        c.close()


def content_triggered():
    """内容触发：新增条数够，或惊讶累积够（意外的事学得更快）。"""
    return (events_since_last() >= config.EVOLVE_EVENT_THRESHOLD
            or surprise_since_last() >= config.SURPRISE_TRIGGER)


def _seconds_since_last():
    last = _state["last_run"] or _last_growth_ts()
    if not last:
        return float("inf")
    try:
        return max(0.0, (_now() - datetime.fromisoformat(last)).total_seconds())
    except Exception:
        return float("inf")


def status():
    last = _state["last_run"] or _last_growth_ts()
    nxt = None
    if enabled() and last:
        try:
            nxt = (datetime.fromisoformat(last)
                   + timedelta(seconds=config.EVOLVE_INTERVAL)).isoformat(timespec="seconds")
        except Exception:
            nxt = None
    return {
        "enabled": enabled(),
        "interval": config.EVOLVE_INTERVAL,
        "threshold": config.EVOLVE_EVENT_THRESHOLD,
        "min_events": config.EVOLVE_MIN_EVENTS,
        "running": _state["running"],
        "runs": _state["runs"],
        "skipped": _state["skipped"],
        "last_run": last,
        "next_run": nxt,
        "last_result": _state["last_result"],
        "last_error": _state["last_error"],
        "events_since": events_since_last(),
        "surprise_trigger": config.SURPRISE_TRIGGER,
        "surprise_since": round(surprise_since_last(), 2),
        "wal_checkpoint_at": (datetime.fromtimestamp(_ckpt_at[0]).isoformat(timespec="seconds")
                              if _ckpt_at[0] else None),
    }


def run_once(force=False):
    """跑一轮代谢；并发调用时立即返回"进行中"。force=True 跳过最小事件数限制。"""
    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "代谢进行中，稍后再试"}
    if not _acquire_file_lock():
        _lock.release()
        return {"ok": True, "skipped": True, "reason": "另一进程正在代谢"}

    try:
        n = _active_events()
        if n < config.EVOLVE_MIN_EVENTS and not force:
            _state["skipped"] += 1
            return {"ok": True, "skipped": True,
                    "reason": "活跃事件仅 %d 条，少于 %d 条不代谢" % (n, config.EVOLVE_MIN_EVENTS)}

        _state["running"] = True
        c = db.connect()
        try:
            r = evolve.tick(c)
        finally:
            c.close()

        snap = r.get("snapshot") or {}
        result = {
            "new_concepts": len(r.get("new_concepts") or []),
            "abstracted": len(r.get("abstracted") or []),
            "archived": (r.get("decay") or {}).get("newly_archived", 0),
            "events": snap.get("events"),
            "concepts": snap.get("concepts"),
            "narrative": bool(r.get("narrative")),
        }
        _state["runs"] += 1
        _state["last_run"] = _now().isoformat(timespec="seconds")
        _state["last_result"] = result
        _state["last_error"] = None
        return {"ok": True, "skipped": False, **result}
    except Exception as e:
        _state["last_error"] = str(e)[:300]
        return {"ok": False, "error": str(e)[:300]}
    finally:
        _state["running"] = False
        _release_file_lock()
        _lock.release()


def maybe_trigger():
    """写入后调用：增量达阈值且距上次代谢够久 → 异步补一轮。"""
    if not enabled() or _state["running"]:
        return False
    try:
        if _active_events() < config.EVOLVE_MIN_EVENTS:
            return False
        if not content_triggered():
            return False
        if _seconds_since_last() < config.EVOLVE_MIN_GAP:
            return False
    except Exception:
        return False
    threading.Thread(target=run_once, name="neuromem-evolve-trigger", daemon=True).start()
    return True


# --- WAL 维护：与代谢解耦（空库、纯读时期 WAL 也会涨，不能只靠代谢轮次） ---
_ckpt_at = [0.0]
CKPT_INTERVAL = 300


def maintain_wal():
    """周期性把 WAL 合并回主库，返回本次是否完成。"""
    now = time.time()
    if now - _ckpt_at[0] < CKPT_INTERVAL:
        return False
    _ckpt_at[0] = now
    c = db.connect()
    try:
        return db.checkpoint(c)
    finally:
        c.close()


def _loop():
    while not _stop.is_set():
        try:
            maintain_wal()
            if enabled() and not _state["running"] and _active_events() >= config.EVOLVE_MIN_EVENTS:
                due = _seconds_since_last() >= config.EVOLVE_INTERVAL
                enough = ((events_since_last() >= config.EVOLVE_EVENT_THRESHOLD
                           or surprise_since_last() >= config.SURPRISE_TRIGGER)
                          and _seconds_since_last() >= config.EVOLVE_MIN_GAP)
                if due or enough:
                    run_once()
        except Exception:
            pass
        _stop.wait(30)


def start():
    global _thread
    if not enabled():
        return False
    if _thread and _thread.is_alive():
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="neuromem-evolve", daemon=True)
    _thread.start()
    return True


def stop():
    _stop.set()