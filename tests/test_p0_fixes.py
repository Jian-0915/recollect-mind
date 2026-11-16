"""P0 修复验证：编辑重算向量 / 图谱过滤归档 / 情感接线 / 自传生成。

独立库 + 真实模型，约 40~70 秒。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import api, db, graph, selfnarrative, store  # noqa: E402

DB = r"D:\neuro-memory\data\p0_test.db"
if os.path.exists(DB):
    os.remove(DB)
conn = db.connect(DB)

print("== 写入 2 条负面记忆 ==")
ids = store.remember(conn, "小明在公园被狗吓到了，他很害怕", speaker="用户")
ids += store.remember(conn, "小明在小巷又被狗追了，更害怕了", speaker="用户")
print(" ids:", ids)

print("\n== P0-2 情感接线 ==")
emo = conn.execute("SELECT valence,arousal,label,updated_at FROM emotion_state WHERE id=1").fetchone()
print(" emotion:", dict(emo))
assert emo is not None, "情感表未初始化"
assert emo["updated_at"], "情感未随写入更新"
assert emo["valence"] < -0.1, "负面记忆后效价应为负，实际 %s" % emo["valence"]

print("\n== P0-4 图谱过滤归档 ==")
ev0 = ids[0]
conn.execute("UPDATE events SET archived=1 WHERE id=?", (ev0,))
conn.commit()
g = graph.build_graph(conn)
print(" 节点数:", len(g.nodes), "| 归档事件在图中:", ev0 in g.nodes)
assert ev0 not in g.nodes, "归档事件不应进入图谱"
conn.execute("UPDATE events SET archived=0 WHERE id=?", (ev0,))
conn.commit()
assert ev0 in graph.build_graph(conn).nodes

print("\n== P0-1 编辑重算向量 ==")
api.conn = lambda: db.connect(DB)
ev1 = ids[1]
before = conn.execute("SELECT embedding FROM events WHERE id=?", (ev1,)).fetchone()[0]
r = api.edit_event(ev1, api.EventEdit(gist="小明在小巷被邻居的狗追，吓得哭了"))
conn2 = db.connect(DB)
after = conn2.execute("SELECT embedding FROM events WHERE id=?", (ev1,)).fetchone()[0]
gist = conn2.execute("SELECT gist FROM events WHERE id=?", (ev1,)).fetchone()[0]
print(" edit:", r, "| gist:", gist)
assert r["ok"] and before != after, "embedding 未重算"
assert r["warnings"], "应提示语义边已移除"
sn = conn2.execute("SELECT COUNT(*) FROM edges WHERE (source=? OR target=?) AND type='semantic'",
                   (ev1, ev1)).fetchone()[0]
print(" 该事件 semantic 边:", sn)
conn2.close()

print("\n== P0-3 自我叙事生成 ==")
text = selfnarrative.build(conn, None)
print(" narrative:", (text or "")[:80])
assert text, "自传应生成非空文本"

print("\n== tick 的 narrative 字段 ==")
from neuromem import evolve  # noqa: E402

res = evolve.tick(conn)
print(" tick keys:", sorted(res.keys()))
assert "narrative" in res

conn.close()
os.remove(DB)
print("\nPASS")