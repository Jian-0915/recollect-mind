"""验证 gist 人称替换：'我'→对话者，但'我们'保留。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import db, store  # noqa: E402

DB = r"D:\neuro-memory\data\pronoun_test.db"
if os.path.exists(DB):
    os.remove(DB)
conn = db.connect(DB)

ids = store.remember(conn, "我和小明是好朋友，我们认识很久了", speaker="用户")
print("ids:", ids)
row = conn.execute("SELECT gist FROM events").fetchone()
gist = row["gist"]
print("gist:", gist)
assert "用户们" not in gist, "「我们」被误替换成「用户们」"
assert "我们认识" in gist, "「我们」应保留"
assert "用户和小明是好朋友" in gist, "「我」未替换为对话者"

rels = conn.execute(
    "SELECT pa.canonical_name a, pb.canonical_name b, r.rel_type t "
    "FROM relations r JOIN personas pa ON r.a=pa.id JOIN personas pb ON r.b=pb.id").fetchall()
print("relations:", [tuple(r) for r in rels])
assert len(rels) == 1 and rels[0]["a"] == "用户" and rels[0]["b"] == "小明", "关系抽取异常"
assert "小红" not in gist

print("PASS")
conn.close()