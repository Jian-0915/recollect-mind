"""成长评测：把发育指标折算成多维度评分，形成"成长仪表盘"。"""
from . import meta


def evaluate(conn):
    m = meta.metrics(conn)
    total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    archived = conn.execute("SELECT COUNT(*) FROM events WHERE archived=1").fetchone()[0]

    dims = {
        "记忆广度": min(1.0, m["events"] / 50.0),
        "抽象能力": min(1.0, m["concepts"] / 10.0 + m["max_level"] / 3.0),
        "社会关系": min(1.0, m["personas"] / 10.0 + m["relations"] / 10.0),
        "活跃度": (m["events"] / total) if total else 0.0,
    }
    score = round(sum(dims.values()) / len(dims) * 100, 1)
    return {
        "score": score,
        "stage": meta.report(conn)["stage"],
        "dimensions": {k: round(v, 2) for k, v in dims.items()},
        "metrics": m,
        "archived": archived,
    }