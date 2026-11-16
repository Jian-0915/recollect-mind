"""关系 CRUD：新增 / 去重更新 / 编辑 / 删除 / 校验（独立库）。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import api, db, persona  # noqa: E402

DB = r"D:\neuro-memory\data\rel_crud_test.db"
if os.path.exists(DB):
    os.remove(DB)
conn = db.connect(DB)
persona.ensure(conn, "小明")
persona.ensure(conn, "小红")
api.conn = lambda: db.connect(DB)

print("== 新增 ==")
r = api.add_relation(api.RelationIn(a="小明", b="小红", type="同事", strength=0.6))
print(" ->", r)
assert r["ok"] and r["updated"] is False

print("\n== 列表带 id 与空时间边界 ==")
rows = api.relations()
print(" ", rows)
assert rows[0]["id"] == r["id"] and rows[0]["since"] == "" and rows[0]["type"] == "同事"

print("\n== 重复新增（反向）→ 只更新强度 ==")
r2 = api.add_relation(api.RelationIn(a="小红", b="小明", type="同事", strength=0.8))
print(" ->", r2)
assert r2["updated"] is True and r2["id"] == r["id"]
row = [x for x in api.relations() if x["id"] == r["id"]][0]
assert abs(row["strength"] - 0.8) < 1e-6

print("\n== 编辑类型 / 强度 / 起始时间 ==")
res = api.edit_relation(r["id"], api.RelationEdit(
    type="朋友", strength=0.9, since="2026-01-01"))
print(" ->", res)
assert res["ok"] and res["updated"] == 1
row = [x for x in api.relations() if x["id"] == r["id"]][0]
print(" ", row)
assert row["type"] == "朋友" and abs(row["strength"] - 0.9) < 1e-6
assert row["since"] == "2026-01-01"

print("\n== 校验 ==")
assert api.add_relation(api.RelationIn(a="小明", b="小明", type="朋友"))["ok"] is False
assert api.add_relation(api.RelationIn(a="", b="小红", type="朋友"))["ok"] is False
assert api.edit_relation(r["id"], api.RelationEdit(type="  "))["ok"] is False

print("\n== 自动创建人物 ==")
r3 = api.add_relation(api.RelationIn(a="王哥", b="小明", type="领导"))
print(" ->", r3)
assert r3["ok"] and persona.find(conn, "王哥") is not None

print("\n== 删除 ==")
assert api.delete_relation(r["id"])["ok"] is True
assert len(api.relations()) == 1
assert api.delete_relation("rel_nope")["ok"] is False

conn.close()
os.remove(DB)
print("\nPASS")