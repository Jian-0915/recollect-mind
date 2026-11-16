"""事件详情接口（独立库，不改正式库）。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import api, db, store  # noqa: E402

DB = r"D:\neuro-memory\data\detail_test.db"
if os.path.exists(DB):
    os.remove(DB)
conn = db.connect(DB)
ids = store.remember(conn, "今天和小明在公园放风筝，他很开心", speaker="用户")
eid = ids[0]
print("created:", eid)

api.conn = lambda: db.connect(DB)   # 让详情接口读测试库
d = api.event_detail(eid)
print("gist:", d["gist"])
print("place:", d["place"], "| affect:", d["affect"])
print("personas:", d["personas"])
print("fragments:", [f["text"] for f in d["fragments"]])
print("concepts:", d["concepts"], "| neighbors:", d["neighbors"])
assert d["ok"] is True
assert d["gist"] and "小明" in d["personas"]
assert d["fragments"], "缺少原文碎片"
assert isinstance(d["neighbors"], list)
assert "embedding" not in d, "向量不应下发"

miss = api.event_detail("evt_not_exist")
print("missing:", miss)
assert miss["ok"] is False

conn.close()
os.remove(DB)
print("PASS")