"""工作记忆接入验证：近期活跃记忆进入工作台并获得召回加成。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import config, db, recall, store, working_memory  # noqa: E402

DB = r"D:\neuro-memory\data\wm_test.db"
if os.path.exists(DB):
    os.remove(DB)
conn = db.connect(DB)

a = store.remember(conn, "小明在公园放风筝，他笑得很开心", speaker="用户")[0]
b = store.remember(conn, "昨天在公司加班到很晚，有点累", speaker="用户")[0]
print("events:", a, b)

wm = working_memory.recent_ids(conn)
print("工作台:", wm)
assert set(wm) == {a, b}, "刚写入的两条都应进入工作台"

print("\n== 过期事件移出工作台 ==")
conn.execute("UPDATE events SET created_at=?, last_recalled_at=NULL WHERE id=?",
             ("2020-01-01T00:00:00+08:00", a))
conn.commit()
wm2 = working_memory.recent_ids(conn)
print("工作台:", wm2)
assert a not in wm2 and b in wm2

print("\n== 容量上限 ==")
from datetime import datetime as _dt  # noqa: E402

now = _dt.now().astimezone().isoformat(timespec="seconds")
for i in range(8):
    conn.execute(
        "INSERT INTO events(id,created_at,gist,activation,salience,archived) "
        "VALUES(?,?,?,?,?,0)", ("w_%d" % i, now, "补充记忆 %d" % i, 1.0, 0.5))
conn.commit()
wm3 = working_memory.recent_ids(conn)
print("工作台数量:", len(wm3), "| 容量:", config.WM_CAPACITY)
assert len(wm3) <= config.WM_CAPACITY

print("\n== recall 标记 working ==")
res = recall.recall(conn, "风筝")
print([(r["gist"][:14], r.get("working")) for r in res[:3]])
assert res, "应有召回结果"
assert all("working" in r for r in res)

print("\n== 加成幅度 ==")
print("WM_BONUS:", config.WM_BONUS)
assert 0 < config.WM_BONUS < 0.2

conn.close()
os.remove(DB)
print("\nPASS")