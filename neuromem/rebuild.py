"""全库向量重建：换嵌入模型后，用新模型为已有记忆重新计算 embedding。

设计要点（对应「遇错即停 + 回滚」需求）：
- 两阶段提交：先读取所有事件的嵌入文本，逐条用新模型算出向量，**全部成功**才进入
  写回阶段；任何一条失败立即停止，且**不写任何一条**回库，原向量保持不变。
- 写回用单事务：要么全成功，要么整体回滚，绝不留下半重建状态。
- 线程内运行，进程内状态供 API 轮询进度；支持中途取消（cooperative）。

嵌入文本口径：写入时 embed.embed(seg) 用的是替换「我」之前的原话片段 seg，
但 seg 未单独入库；这里用 gist（保留完整语义的原文证据）作为近似文本。
"""
import threading
import time

from . import db, embed, settings

_lock = threading.Lock()          # 同一时刻只允许一个重建任务
_state = {
    "running": False,
    "phase": "idle",              # idle | embedding | writing | done | error | cancelled
    "done": 0,
    "total": 0,
    "model": "",
    "dim": 0,
    "error": None,
    "started_at": None,
    "finished_at": None,
    "elapsed_ms": 0,
    "rebuilt": 0,                 # 实际写回条数
}
_cancel = threading.Event()
_thread = None


def state():
    """进度快照（供 API 返回）。"""
    with _lock:
        s = dict(_state)
    return s


def _set(**kw):
    with _lock:
        _state.update(kw)


def start():
    """启动重建。返回 (ok, message)。已有任务在跑则拒绝。"""
    global _thread
    with _lock:
        if _state["running"]:
            return False, "已有重建任务正在运行"
    cfg = settings.current()["embed"]
    model = cfg.get("model") or ""
    if not model:
        return False, "嵌入模型未配置"
    _cancel.clear()
    _set(running=True, phase="embedding", done=0, total=0, model=model,
         dim=cfg.get("dim") or 0, error=None, started_at=time.time(),
         finished_at=None, elapsed_ms=0, rebuilt=0)
    _thread = threading.Thread(target=_run, args=(cfg,), daemon=True)
    _thread.start()
    return True, "已启动"


def cancel():
    """请求取消（协作式：在处理下一条前检查）。"""
    if not _state["running"]:
        return False, "没有正在运行的重建任务"
    _cancel.set()
    return True, "已请求取消"


def _run(cfg):
    t0 = time.time()
    # 自重置运行时字段：即使被直接调用（测试 / 手动）也不残留上一次的状态
    _set(rebuilt=0, done=0, total=0, error=None,
         model=cfg.get("model") or "", dim=cfg.get("dim") or 0)
    try:
        conn = db.connect()
        try:
            rows = conn.execute(
                "SELECT id, gist FROM events WHERE gist IS NOT NULL AND gist!='' ORDER BY created_at"
            ).fetchall()
        finally:
            conn.close()
        total = len(rows)
        _set(total=total)
        if total == 0:
            _set(phase="done", running=False, finished_at=time.time(),
                 elapsed_ms=int((time.time() - t0) * 1000))
            return

        # 阶段 1：全部算出，任何失败即停，不写库
        vectors = []  # [(id, [float,...])]
        for i, row in enumerate(rows):
            if _cancel.is_set():
                _set(phase="cancelled", running=False, finished_at=time.time(),
                     elapsed_ms=int((time.time() - t0) * 1000))
                return
            try:
                vec = embed.embed(row["gist"])
            except Exception as e:
                _set(phase="error", running=False, error="第 %d/%d 条嵌入失败：%s"
                     % (i + 1, total, str(e)[:200]), finished_at=time.time(),
                     elapsed_ms=int((time.time() - t0) * 1000))
                return  # 不写任何一条
            vectors.append((row["id"], vec))
            _set(done=i + 1)

        if _cancel.is_set():
            _set(phase="cancelled", running=False, finished_at=time.time(),
                 elapsed_ms=int((time.time() - t0) * 1000))
            return

        # 阶段 2：单事务写回，保证原子性
        _set(phase="writing")
        conn = db.connect()
        try:
            conn.execute("BEGIN")
            for eid, vec in vectors:
                conn.execute("UPDATE events SET embedding=? WHERE id=?",
                             (db.pack(vec), eid))
            conn.commit()
        except Exception as e:
            conn.rollback()
            _set(phase="error", running=False, error="写回失败已回滚：%s" % str(e)[:200],
                 finished_at=time.time(), elapsed_ms=int((time.time() - t0) * 1000))
            return
        finally:
            conn.close()

        _set(phase="done", running=False, rebuilt=len(vectors),
             finished_at=time.time(), elapsed_ms=int((time.time() - t0) * 1000))
    except Exception as e:
        _set(phase="error", running=False, error=str(e)[:200],
             finished_at=time.time(), elapsed_ms=int((time.time() - t0) * 1000))
