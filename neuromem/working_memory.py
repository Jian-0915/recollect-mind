"""注意力 / 工作记忆 / 目标系统（轻量版）。

- GoalManager / WorkingMemory：可选的内存工具（当前无"目标"来源，未接入主流程）
- recent_ids：**已接入 recall** 的短期工作台——最近写入或被召回的记忆获得小加成

接入原则：只做"近期活跃优先"，不引入用户配置，避免变成不可解释的加权。
"""
from datetime import datetime, timedelta


def recent_ids(conn, hours=24, limit=6):
    """最近 hours 小时内写入或召回过的活跃记忆 id（按时间倒序）。"""
    since = (datetime.now().astimezone() - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT id FROM events WHERE archived=0 "
        "AND (created_at >= ? OR (last_recalled_at IS NOT NULL AND last_recalled_at >= ?)) "
        "ORDER BY COALESCE(last_recalled_at, created_at) DESC LIMIT ?",
        (since, since, limit)).fetchall()
    return [r["id"] for r in rows]


class GoalManager:
    def __init__(self):
        self._stack = []

    def push(self, goal):
        if goal:
            self._stack.append(goal)
        return self.current()

    def pop(self):
        return self._stack.pop() if self._stack else None

    def current(self):
        return self._stack[-1] if self._stack else None

    def stack(self):
        return list(self._stack)


class WorkingMemory:
    """有界工作台：目标相关的记忆优先进入，其余按分数。"""

    def __init__(self, capacity=6, goal_bonus=0.35):
        self.capacity = capacity
        self.goal_bonus = goal_bonus
        self.slots = []
        self.goal = None

    def fill(self, items, goal=None):
        self.goal = goal
        scored = []
        for it in items:
            s = float(it.get("score", 0.0) or 0.0)
            if goal:
                text = (it.get("gist") or "") + " " + " ".join(it.get("fragments") or [])
                if goal and goal in text:
                    s += self.goal_bonus
            scored.append((s, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        self.slots = [it for _, it in scored[: self.capacity]]
        return self.slots

    def context(self):
        return "\n".join(
            "%d.[%s]%s" % (i, it.get("kind", ""), it.get("gist", ""))
            for i, it in enumerate(self.slots, 1)
        )

    def clear(self):
        self.slots = []

    def __len__(self):
        return len(self.slots)