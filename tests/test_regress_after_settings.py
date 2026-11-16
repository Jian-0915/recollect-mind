"""改造后写入/检索/回答链路回归（独立库，不污染正式库）。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import db, query, store  # noqa: E402

DB = r"D:\neuro-memory\data\regress.db"
if os.path.exists(DB):
    os.remove(DB)
conn = db.connect(DB)

ids = store.remember(conn, "今天和小明在公园放风筝，他很开心", speaker="用户")
print("remember ids:", ids)
row = conn.execute("SELECT gist, place FROM events").fetchone()
print("gist:", row["gist"], "| place:", row["place"])
assert row["gist"]

r = query.query(conn, "小明今天开心吗")
print("hits:", len(r["events"]), "| summary:", (r["summary"] or "")[:120])
assert r["events"]
assert r["summary"]

print("PASS（写入 / 检索 / 回答链路正常）")
conn.close()
os.remove(DB)