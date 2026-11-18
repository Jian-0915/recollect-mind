"""F5：她自己发现缺口、主动去查外部信息。

设计红线（对应 ROADMAP F5 验收）：
  - 不打扰：只在对话里出现「有意义的未知实体」时才动，没有明确实体不查；
    静默时段不查；每天有上限、两次之间有最小间隔。
  - 可一键关：NEUROMEM_AUTORESEARCH=0 或 runtime_off() 立刻停。
  - 脱敏：只拿抽出来的实体词去查，绝不把整段私聊发出去。
  - 可审计：每次自主查询写独立知识库的 autoresearch_log（何时/为何/查了什么）。
  - 不污染：查到的东西进独立知识库，绝不写主库 events/concepts。

链路：
    对话后 observe() → discover() 抽实体并查知识库去重 → should_search() 过闸
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
    return out


def should_search(kb_conn, now: datetime | None = None) -> tuple[bool, str]:
    """触发闸：开关 / 静默 / 每日上限 / 最小间隔。返回 (可否, 原因)。"""
    if not config.AUTORESEARCH:
        return False, "自主查询已关闭"
    if _runtime_off.is_set():
        return False, "自主查询已关闭（本次运行）"
    now = now or _now()
    if _quiet(now):
        return False, "静默时段不查"
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
        }
    except Exception:
        return {"enabled": enabled(), "daily_cap": config.AUTORESEARCH_DAILY,
                "gap_min": config.AUTORESEARCH_GAP_MIN,
                "today_count": 0, "last_at": ""}
    finally:
        if own and conn is not None:
            conn.close()


def _run_once(text: str, private_terms, source_event: str, provider=None):
    """后台真正执行的一次自主查询。任何异常都吞掉并写审计，绝不影响对话。"""
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
        # 一次只查一个最相关的实体（多条实体分次查，受频次上限约束）
        cand = candidates[0]
        entity = cand["entity"]
        result = _research.research(
            entity, kb_conn=conn, private_terms=private_terms, provider=provider,
            max_results=3, generate_fn=lambda p: "")  # 自主查询不需要生成回答，只要存资料
        hit = len(result.sources)
        knowledge.log_autoresearch(
            conn, entity=entity, query=result.query or entity,
            reason=cand["reason"], hit_count=hit,
            status="ok" if hit else "empty", source_event=source_event)
        return {"entity": entity, "hits": hit}
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def observe(text: str, private_terms=(), source_event: str = "", provider=None,
            *, sync: bool = False):
    """对话后调用：发现缺口→过闸→后台查→存库+审计。不阻塞、不抛出异常。

    sync=True 时同步执行（仅测试用），返回执行结果；正常走守护线程返回 None。
    """
    if not enabled():
        return None
    # 候选判定放在 _run_once 内部（它自己开连接、自己 discover）。
    # 曾在这里做预筛并 close 共享连接，导致注入的连接被提前关掉——去掉。
    if sync:
        with _exec_lock:
            return _run_once(text, private_terms, source_event, provider)

    def _worker():
        # 拿不到锁就跳过这轮（上一轮还在查），不排队堆积
        if not _exec_lock.acquire(blocking=False):
            return
        try:
            _run_once(text, private_terms, source_event, provider)
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
