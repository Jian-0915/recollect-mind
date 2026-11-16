"""最近几轮已被使用过的记忆 id（只读工具）。

供两处共用：
- ``recall.recall`` 的疲劳度降权（F3）
- ``companion.surface`` 的「刚聊过就别再提」去重（F5）

2026-11-15 长测暴露：``surface`` 原先只看 ``companion_state.surfaced_ids``，
而该字段只有调用 ``surface()`` 时才写；绝大多数轮次陪伴侧并不调用它，
于是「刚聊过什么」这个信息在库里明明存在（conversation_turns.event_ids
与 adoption_log.used_ids），却从未参与去重。

只依赖调用方传入的 sqlite 连接；表缺失或 JSON 损坏时静默返回空集，
绝不打断主链路。
"""
import json


def used_ids(conn, turns=6):
    """返回最近 ``turns`` 轮被引用 / 被采纳过的事件 id 集合。"""
    out = set()
    n = max(1, int(turns or 6))
    queries = (
        "SELECT event_ids FROM conversation_turns "
        "ORDER BY created_at DESC, ts DESC LIMIT ?",
        "SELECT used_ids FROM adoption_log WHERE source='agent' "
        "ORDER BY id DESC LIMIT ?",
    )
    for sql in queries:
        try:
            for row in conn.execute(sql, (n,)):
                v = json.loads(row[0] or "[]")
                if isinstance(v, list):
                    out.update(str(x) for x in v if x)
        except Exception:
            continue
    return out
