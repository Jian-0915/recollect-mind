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


def status(kb_conn=None) -> dict:
    """给前端看的状态：开关、今日次数、最近时间。"""
    own = kb_conn is None
    conn = kb_conn or knowledge.connect()
    try:
        now = _now()
        since = (now - timedelta(days=1)).isoformat()
        return {
            "enabled": enabled(),
            "daily_cap": config.AUTORESEARCH_DAILY,
            "gap_min": config.AUTORESEARCH_GAP_MIN,
            "today_count": knowledge.autoresearch_count_since(conn, since),
            "last_at": knowledge.autoresearch_last_at(conn),
            "brief": knowledge.brief_stats(conn),
        }
    except Exception:
        return {"enabled": enabled(), "daily_cap": config.AUTORESEARCH_DAILY,
                "gap_min": config.AUTORESEARCH_GAP_MIN,
                "today_count": 0, "last_at": "",
                "brief": {"total": 0, "pending": 0}}
    finally:
        if own and conn is not None:
            conn.close()


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


def start():
    """占位：F5 的触发挂在对话主链路（observe），无需常驻线程。
    保留此接口与 autoevolve.start() 对称，供统一启停调用。"""
    return enabled()


def stop():
    """运行期关闭（幂等）。"""
    set_enabled(False)
