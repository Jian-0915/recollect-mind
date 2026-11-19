"""系统运行视图（v47）——把散落各处的运行时状态聚合成一个只读快照。

为什么要独立一层：此前「她现在在干嘛 / 待查队列有什么 / 聊到哪了」分散在
autoresearch.status、live.status、knowledge 的几张表和 companion.state 里，
没有一处能看到全貌。用户要一个和管理页区分开的「系统运行界面」，这一层
就是它的数据源。

铁律：
  - 全只读——绝不写主库、绝不消费播报（不能像 /api/autoresearch/brief 那样
    GET 即标记 delivered，否则打开监控页就把待播报的消息偷走了）；
  - 每个子系统单独 try，坏一个不影响其它——监控页自己不能先崩；
  - 不返回完整私聊原文，open_topics 是话题摘要、brief 文本已截断。
"""
from __future__ import annotations

from datetime import datetime

from . import autoresearch, knowledge, live
from . import db as _db
from . import companion as _companion


def _safe(fn, default):
    """异常隔离：某块数据坏了也要带上 error 返回，让监控页看得见故障。"""
    try:
        return fn()
    except Exception as exc:
        err = "%s: %s" % (type(exc).__name__, str(exc)[:120])
        base = dict(default) if isinstance(default, dict) else {}
        base["error"] = err
        return base


def _pipeline_stage(kb_conn, ar_status: dict, backlog: list[dict]) -> dict:
    """推断自主查询流水线当前停在哪一步，给人话说明。"""
    pending = [t for t in backlog if t.get("status") == "pending"]
    stats = ar_status if isinstance(ar_status, dict) else {}
    if not stats.get("enabled"):
        return {"stage": "off", "label": "自主获取已关闭", "detail": "设置里可重新打开"}
    if pending:
        t = pending[0]
        return {"stage": "queued", "label": "待查队列里有主题等着查",
                "detail": "%s（%s）" % (t.get("entity", ""), t.get("reason", ""))}
    if not stats.get("loop_alive"):
        return {"stage": "idle", "label": "常驻循环未运行",
                "detail": "重启桌面端后会启动；有承诺入队时也会唤醒"}
    last = stats.get("last_at") or ""
    gap = "%s 最近一次查询" % last[11:19] if len(last) >= 19 else "还没有查询记录"
    return {"stage": "watching", "label": "常驻循环运行中，等待触发",
            "detail": "%s；每 %s 分钟扫一轮话题" % (gap, stats.get("tick_min", "?"))}


def snapshot() -> dict:
    """系统运行全貌快照。只读、异常隔离；前端定时轮询这个方法对应的接口。"""
    out = {
        "now": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ok": True,
    }

    # 1) 自主获取：开关 / 循环 / 配额 / 今日次数
    ar = _safe(lambda: autoresearch.status(), {})
    out["autoresearch"] = ar

    # 2) 知识库：待查队列、播报、审计——只读列表，绝不 mark delivered
    def _kb_part():
        c = knowledge.connect()
        try:
            return {
                "backlog": knowledge.list_backlog(c, 50),
                "briefs": knowledge.list_briefs(c, 50),
                "log": knowledge.list_autoresearch(c, 30),
                "stats": knowledge.stats(c),
                "brief_stats": knowledge.brief_stats(c),
                "backlog_stats": knowledge.backlog_stats(c),
            }
        finally:
            c.close()

    kb = _safe(_kb_part, {})
    out["knowledge"] = kb

    # 3) 流水线当前阶段（基于上面两份数据推断）
    out["pipeline"] = _pipeline_stage(
        None, ar, (kb.get("backlog") if isinstance(kb, dict) else None) or [])

    # 4) 聊天侧：对话统计 + 正在聊的主题（open_topics）
    def _chat_part():
        info = live.status()
        c = _db.connect()
        try:
            st = _companion.state(c)
            info["open_topics"] = list(st.get("open_topics") or [])
        finally:
            c.close()
        return info

    out["chat"] = _safe(_chat_part, {})
    return out
