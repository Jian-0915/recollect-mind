"""表征漂移（受控）：让"每次回忆都微微重编码"，但用整体平移 + 锚点 + 自相似阈值保住"我"。

与旧版的区别（旧版是每条独立加噪、全量 O(N)）：
- 每轮只动 `n` 条（默认 5），避免写放大
- 所有被漂移的向量用**同一个方向**（整体平移，近似保持相对几何）
- 锚点（activation 最高的 3 条）不动
- 漂移后与原向量的余弦必须 ≥ `min_sim`，否则该条不写回
- 每次记录 `drift_log`，可追溯
"""
import random

from . import db

ANCHORS = 3


def _norm(v):
    return sum(x * x for x in v) ** 0.5


def _cos(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    na, nb = _norm(a), _norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def drift(conn, n=5, magnitude=0.01, min_sim=0.995):
    """返回 {"changed": k, "magnitude": m}。"""
    anchors = {r["id"] for r in conn.execute(
        "SELECT id FROM events WHERE archived=0 AND embedding IS NOT NULL "
        "ORDER BY COALESCE(activation,1.0) DESC, COALESCE(salience,0) DESC LIMIT ?",
        (ANCHORS,))}
    rows = [r for r in conn.execute(
        "SELECT id,embedding FROM events WHERE archived=0 AND embedding IS NOT NULL "
        "ORDER BY RANDOM() LIMIT ?", (n + len(anchors),)) if r["id"] not in anchors][:n]
    if not rows:
        return {"changed": 0, "magnitude": magnitude}

    first = db.unpack(rows[0]["embedding"])
    if not first:
        return {"changed": 0, "magnitude": magnitude}
    delta = [random.gauss(0, 1) for _ in range(len(first))]
    dn = _norm(delta) or 1.0
    delta = [x / dn for x in delta]

    changed = 0
    for r in rows:
        v = db.unpack(r["embedding"])
        if not v or len(v) != len(delta):
            continue
        vn = _norm(v)
        if vn == 0:
            continue
        scale = magnitude * vn
        moved = [x + d * scale for x, d in zip(v, delta)]
        mn = _norm(moved) or 1.0
        nv = [x * vn / mn for x in moved]
        if _cos(v, nv) < min_sim:
            continue  # 自相似跌破阈值：这条不漂（刹车）
        conn.execute("UPDATE events SET embedding=? WHERE id=?", (db.pack(nv), r["id"]))
        changed += 1

    if changed:
        from datetime import datetime
        ts = datetime.now().astimezone().isoformat(timespec="seconds")
        conn.execute("INSERT INTO drift_log(ts,n,magnitude) VALUES(?,?,?)",
                     (ts, changed, magnitude))
    conn.commit()
    return {"changed": changed, "magnitude": magnitude}