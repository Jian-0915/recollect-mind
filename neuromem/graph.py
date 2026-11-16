"""从 edges 表构图，并做受控扩散激活。

图结构按 (活跃事件数, 边数) 缓存：每次都重建 5 万节点的 NetworkX 图要 200ms+，
而检索请求是读多写少。数量变化（增删事件/边）时缓存自动失效。
"""
import threading

import networkx as nx

_LOCK = threading.Lock()
_CACHE = {"key": None, "g": None}


def build_graph(conn):
    n_events = conn.execute("SELECT COUNT(*) FROM events WHERE archived=0").fetchone()[0]
    n_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    # 只按「活跃事件数 + 边数」做 key 会在清库后撞车：旧库 709 条 → 清空 →
    # 重新写入恰好 709 条时，另一个进程里的旧图会被直接复用。加上最新写入时间，
    # 清库后新数据的时间戳必然不同，跨进程缓存一定失效。
    max_created = conn.execute("SELECT MAX(created_at) FROM events").fetchone()[0] or ""
    row = conn.execute("PRAGMA database_list").fetchone()
    db_path = row[2] if row else ""
    key = (db_path, n_events, n_edges, max_created)

    with _LOCK:
        if _CACHE["key"] == key and _CACHE["g"] is not None:
            return _CACHE["g"]

    g = nx.Graph()
    for r in conn.execute("SELECT id FROM events WHERE archived=0"):
        g.add_node(r["id"], kind="event")
    for r in conn.execute("SELECT id FROM personas"):
        g.add_node(r["id"], kind="persona")
    for r in conn.execute("SELECT source,target,type,weight FROM edges"):
        u, v, t, w = r["source"], r["target"], r["type"], r["weight"] or 0.0
        # 只加两端都存在的边：add_edge 会自动建节点，归档事件会被重新带进来
        if not (g.has_node(u) and g.has_node(v)):
            continue
        if g.has_edge(u, v):
            g[u][v]["weight"] = max(g[u][v].get("weight", 0.0), w)
        else:
            g.add_edge(u, v, type=t, weight=w)

    with _LOCK:
        _CACHE["key"] = key
        _CACHE["g"] = g
    return g


def invalidate():
    with _LOCK:
        _CACHE["key"] = None
        _CACHE["g"] = None


def spread(g, seeds, hops=1, decay=0.6):
    """从 seeds {node: score} 出发做受控多跳扩散，返回 {node: score}。"""
    scores = {}
    frontier = {}
    for n, s in seeds.items():
        if n in g:
            scores[n] = max(scores.get(n, 0.0), s)
            frontier[n] = s
    for _ in range(hops):
        nxt = {}
        for n, s in frontier.items():
            for nb in g.neighbors(n):
                w = g[n][nb].get("weight", 0.5) or 0.5
                ns = s * decay * max(0.0, min(1.0, w))
                if ns > scores.get(nb, 0.0):
                    nxt[nb] = max(nxt.get(nb, 0.0), ns)
        for n, s in nxt.items():
            scores[n] = max(scores.get(n, 0.0), s)
        frontier = nxt
        if not frontier:
            break
    return scores