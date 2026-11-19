"""F5：她自己发现缺口、主动去查外部信息。

设计红线（对应 ROADMAP F5 验收）：
  - 需求驱动：对话里出现「有意义的未知实体」，或在问外部信息（「贵州有什么
    景点」）时才动。后者从问句里提炼脱敏主题词，绝不把整段私聊发出去；
    这是 2026-11-19 v44 补的第二条路径——旧版只认带地名后缀的专名，
    问「有什么推荐」查不到，她转头就用参数知识编答案。没有需求不查。
    这是异步后台行为——不弹窗、不占对话、不打断用户，所以 2026-11-19 起
    取消静默时段，每日上限与间隔只作防抖/防配额打爆，不再当防打扰闸门。
  - 可一键关：NEUROMEM_AUTORESEARCH=0 或 runtime_off() 立刻停。
  - 脱敏：只拿抽出来的实体词去查，绝不把整段私聊发出去。
  - 可审计：每次自主查询写独立知识库的 autoresearch_log（何时/为何/查了什么）。
  - 不污染：查到的东西进独立知识库，绝不写主库 events/concepts。

链路：
    对话后 observe() → discover() 抽实体 + 问句主题，查知识库去重 → should_search() 过闸
    → 守护线程执行 research.research(force=True) → 写知识库 + 审计日志

本模块所有函数都容忍异常、静默降级——自主获取失败绝不能影响正常对话。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from . import config, intent, knowledge

# 运行期开关（前端可关，不写主库、进程内有效）
_runtime_off = threading.Event()

# 执行串行化：自主查询在守护线程里跑，别并发打爆搜索服务
_exec_lock = threading.Lock()


def enabled() -> bool:
    return bool(config.AUTORESEARCH) and not _runtime_off.is_set()


def set_enabled(on: bool):
    """前端开关：on=False 立刻停（只影响本进程，重启后回到 config 默认）。"""
    if on:
        _runtime_off.clear()
    else:
        _runtime_off.set()


def _now():
    return datetime.now(timezone.utc).astimezone()


def _quiet(now: datetime) -> bool:
    """跨午夜的静默时段，和 motivation 保持一致：不在此刻打扰。"""
    a, b = config.AUTORESEARCH_QUIET_FROM, config.AUTORESEARCH_QUIET_TO
    if a == b:
        return False
    h = now.hour
    if a < b:
        return a <= h < b
    return h >= a or h < b


def discover(text: str, kb_conn, private_terms=()) -> list[dict]:
    """从一句话里找「库里还没有的未知实体」。纯本地、零网络。

    返回 [{entity, reason}]；没有可查实体或库里已有则空列表。
    """
    out: list[dict] = []
    try:
        entities = intent.extract_entities(text, private_terms)
    except Exception:
        return out
    for e in entities:
        e = str(e).strip()
        if len(e) < 2:
            continue
        try:
            if knowledge.has_knowledge(kb_conn, e):
                continue
        except Exception:
            continue
        out.append({"entity": e, "reason": "对话里出现未知实体"})

    # v44：实体路径之外再补一条「信息需求」路径。旧版只认带地名后缀的专名，
    # 「贵州有什么景点」「这边有哪些好玩的」一句都抽不到，于是模型转头就用
    # 参数知识编出景点名——用户正是这样发现的（09:03 / 09:26）。
    try:
        topic = intent.extract_info_query(text, private_terms)
    except Exception:
        topic = ""
    if topic and 2 <= len(topic) <= 16:
        try:
            known = any((topic in str(x["entity"]) or str(x["entity"]) in topic)
                        for x in out)
            if not known and not knowledge.has_knowledge(kb_conn, topic):
                out.append({"entity": topic, "reason": "对话里在问外部信息"})
        except Exception:
            pass
    return out


def should_search(kb_conn, now: datetime | None = None) -> tuple[bool, str]:
    """触发闸：开关 / 每日上限 / 最小间隔。返回 (可否, 原因)。

    2026-11-19：已移除静默时段闸门。后台查询不弹窗、不占对话、不打断用户，
    没有任何理由在深夜停摆——用户明确要求需求驱动、全天候。
    """
    if not config.AUTORESEARCH:
        return False, "自主查询已关闭"
    if _runtime_off.is_set():
        return False, "自主查询已关闭（本次运行）"
    now = now or _now()
    try:
        # 每日上限：过去 24 小时的审计条数
        since = (now - timedelta(days=1)).isoformat()
        if knowledge.autoresearch_count_since(kb_conn, since) >= config.AUTORESEARCH_DAILY:
            return False, "今天自主查询已达上限"
        # 最小间隔
        last = knowledge.autoresearch_last_at(kb_conn)
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                gap_min = (now - last_dt).total_seconds() / 60.0
                if gap_min < config.AUTORESEARCH_GAP_MIN:
                    return False, "离上次自主查询太近"
            except (ValueError, TypeError):
                pass
    except Exception:
        return False, "闸门检查失败"
    return True, "可以查"


def _run_once(text: str, private_terms, source_event: str, provider=None,
              generate_fn=None):
    """后台真正执行的一次自主查询。任何异常都吞掉并写审计，绝不影响对话。

    需求驱动：一轮对话里发现几个未知实体就查几个（受每日上限约束），
    查到的资料用对话模型整理成一句她口吻的话，写进播报队列供聊天页显示。
    """
    conn = None
    try:
        from . import research as _research
        conn = knowledge.connect()
        candidates = discover(text, conn, private_terms)
        if not candidates:
            return None
        ok, why = should_search(conn)
        if not ok:
            return None
        # 需求驱动：把本轮发现的全部候选都查一遍，不再只查第一个。
        # should_search 的每日上限按「轮」粗放；这里再按「条」精确兜底，
        # 防止一句话里塞十几个实体一次性打爆 API 配额。
        since = (_now() - timedelta(days=1)).isoformat()
        used = knowledge.autoresearch_count_since(conn, since)
        room = max(0, int(config.AUTORESEARCH_DAILY) - used)
        limit = min(config.AUTORESEARCH_PER_TURN_MAX, room)
        results = []
        for cand in candidates[:limit]:
            entity = cand["entity"]
            try:
                result = _research.research(
                    entity, kb_conn=conn, private_terms=private_terms,
                    provider=provider, max_results=3, force=True)
                hit = len(result.sources)
                knowledge.log_autoresearch(
                    conn, entity=entity, query=result.query or entity,
                    reason=cand["reason"], hit_count=hit,
                    status="ok" if hit else "empty", source_event=source_event)
                if hit:
                    # 用模型把资料整理成一句她口吻的话 → 播报队列（不写主库）
                    brief = _research.summarize_discovery(
                        entity, result.sources, generate_fn=generate_fn)
                    if brief:
                        knowledge.add_brief(
                            conn, entity=entity, text=brief,
                            sources=[{"title": s.title, "url": s.url}
                                     for s in result.sources[:3]])
                results.append({"entity": entity, "hits": hit})
            except Exception:
                # 单个实体失败不影响其它实体，也不影响对话
                continue
        return results[0] if results else None
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def observe(text: str, private_terms=(), source_event: str = "", provider=None,
            generate_fn=None, *, sync: bool = False):
    """对话后调用：发现缺口→过闸→后台查→存库+审计。不阻塞、不抛出异常。

    sync=True 时同步执行（仅测试用），返回执行结果；正常走守护线程返回 None。
    """
    if not enabled():
        return None
    # 候选判定放在 _run_once 内部（它自己开连接、自己 discover）。
    # 曾在这里做预筛并 close 共享连接，导致注入的连接被提前关掉——去掉。
    if sync:
        with _exec_lock:
            return _run_once(text, private_terms, source_event, provider, generate_fn)

    def _worker():
        # 拿不到锁就跳过这轮（上一轮还在查），不排队堆积
        if not _exec_lock.acquire(blocking=False):
            return
        try:
            _run_once(text, private_terms, source_event, provider, generate_fn)
        finally:
            _exec_lock.release()

    threading.Thread(target=_worker, name="neuromem-autoresearch", daemon=True).start()
    return None


def enqueue_promise(user_text: str, reply: str, private_terms=(),
                    source_event: str = "", history=()) -> int:
    """她说「我去查查」→ 排进待查队列。不做网络、不阻塞对话。

    live.py 在每轮回复后调用。整段包 try：兑现承诺的副作用绝不能影响对话。
    history 是最近几轮用户原话——本轮只回「就两天」时靠它回溯主题。
    """
    if not enabled():
        return 0
    try:
        from . import promise as _promise
        topic = _promise.promised_topic(
            user_text, reply, private_terms, history=history)
        if not topic:
            return 0
        conn = knowledge.connect()
        try:
            tid = knowledge.enqueue_research(
                conn, entity=topic, reason="她答应去查", source_event=source_event)
            if tid:
                _wake.set()   # 唤醒常驻循环，尽快兑现，不等下一轮对话
            return tid
        finally:
            conn.close()
    except Exception:
        return 0


# --- 常驻循环（v45）：不以对方说话为触发条件 -------------------------------
_stop = threading.Event()
_wake = threading.Event()
_thread: threading.Thread | None = None
_loop_lock = threading.Lock()


def _open_topics(main_conn=None) -> list[str]:
    """只读主库拿「没聊完的话题」，绝不写主库。"""
    own = main_conn is None
    conn = main_conn
    try:
        if own:
            from . import db
            conn = db.connect()
        from . import companion as _companion
        return [str(x)[:60] for x in (_companion.state(conn).get("open_topics") or [])]
    except Exception:
        return []
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _enqueue_open_topics(conn, private_terms=()) -> int:
    """从没聊完的话题里挑像「外部事实」的入队。私人话题抽不出主题就跳过。"""
    from . import intent as _intent
    n = 0
    for topic in _open_topics()[:5]:
        try:
            q = _intent.extract_info_query(topic, private_terms)
            ents = _intent.extract_entities(topic, private_terms)
            entity = str(q or (ents[0] if ents else "")).strip()
            if not (2 <= len(entity) <= 16):
                continue
            if knowledge.has_knowledge(conn, entity):
                continue
            if knowledge.enqueue_research(conn, entity=entity,
                                          reason="没聊完的话题"):
                n += 1
        except Exception:
            continue
    return n


def tick_once(*, provider=None, generate_fn=None) -> dict:
    """跑一轮常驻循环：兑现队列里的承诺 + 扫一次没聊完的话题。

    返回统计供测试与审计。任何异常都吞掉——后台任务绝不能影响对话。
    """
    if not enabled():
        return {"ran": False, "reason": "自主查询已关闭"}
    if not _exec_lock.acquire(blocking=False):
        return {"ran": False, "reason": "上一轮查询还在跑"}
    out = {"ran": True, "queued": 0, "searched": 0, "hits": 0, "skipped": 0}
    conn = None
    try:
        from . import research as _research
        conn = knowledge.connect()
        if config.AUTORESEARCH_TOPIC_SCAN:
            from .research import private_terms_from_db
            try:
                out["queued"] = _enqueue_open_topics(
                    conn, private_terms_from_db())
            except Exception:
                pass
        tasks = knowledge.pending_research(conn, config.AUTORESEARCH_BACKLOG_MAX)
        for t in tasks:
            ok, why = should_search(conn)
            if not ok:
                out["skipped"] += 1
                knowledge.bump_research(conn, t["id"])
                continue
            try:
                result = _research.research(
                    t["entity"], kb_conn=conn,
                    private_terms=(),
                    provider=provider, max_results=3, force=True,
                    generate_fn=generate_fn)
                hit = len(result.sources)
                knowledge.log_autoresearch(
                    conn, entity=t["entity"], query=result.query or t["entity"],
                    reason=t.get("reason") or "待查队列", hit_count=hit,
                    status="ok" if hit else "empty",
                    source_event=t.get("source_event") or "")
                if hit:
                    brief = _research.summarize_discovery(
                        t["entity"], result.sources, generate_fn=generate_fn)
                    if brief:
                        knowledge.add_brief(
                            conn, entity=t["entity"], text=brief,
                            sources=[{"title": s.title, "url": s.url}
                                     for s in result.sources[:3]])
                    out["hits"] += 1
                knowledge.mark_research(conn, t["id"])
                out["searched"] += 1
            except Exception:
                tries = knowledge.bump_research(conn, t["id"])
                if tries >= 3:
                    knowledge.mark_research(conn, t["id"], status="failed")
                out["skipped"] += 1
        return out
    except Exception:
        return out
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _exec_lock.release()


def _loop():
    while not _stop.wait(max(0.1, config.AUTORESEARCH_TICK_MIN) * 60):
        _wake.wait(2.0)   # 承诺入队后短暂 coalesce，避免连着查同一批
        _wake.clear()
        try:
            tick_once()
        except Exception:
            pass


def start():
    """启动后台常驻循环：兑现承诺 + 定期扫没聊完的话题。

    原来只是占位（触发挂在对话后），v45 起真正起线程——她不再等对方说话。
    """
    global _thread
    if not enabled():
        return False
    if _thread and _thread.is_alive():
        return False
    _stop.clear()
    _wake.set()   # 启动时先跑一轮，把重启前排的队兑现掉
    _thread = threading.Thread(target=_loop, name="neuromem-autoresearch-loop",
                               daemon=True)
    _thread.start()
    return True


def stop():
    """运行期关闭（幂等）：停线程 + 关开关。"""
    _stop.set()
    _wake.set()
    set_enabled(False)


def status(kb_conn=None) -> dict:
    """给前端看的状态：开关、今日次数、最近时间、待查队列。"""
    own = kb_conn is None
    conn = kb_conn or knowledge.connect()
    try:
        now = _now()
        since = (now - timedelta(days=1)).isoformat()
        return {
            "enabled": enabled(),
            "loop_alive": bool(_thread and _thread.is_alive()),
            "tick_min": config.AUTORESEARCH_TICK_MIN,
            "daily_cap": config.AUTORESEARCH_DAILY,
            "gap_min": config.AUTORESEARCH_GAP_MIN,
            "today_count": knowledge.autoresearch_count_since(conn, since),
            "last_at": knowledge.autoresearch_last_at(conn),
            "brief": knowledge.brief_stats(conn),
            "backlog": knowledge.backlog_stats(conn),
        }
    except Exception:
        return {"enabled": enabled(), "daily_cap": config.AUTORESEARCH_DAILY,
                "gap_min": config.AUTORESEARCH_GAP_MIN,
                "today_count": 0, "last_at": "",
                "brief": {"total": 0, "pending": 0},
                "backlog": {"total": 0, "pending": 0}}
    finally:
        if own and conn is not None:
            conn.close()
