"""概念 / 人物删除：级联清理与强制解除绑定（独立库）。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import api, db, persona, store  # noqa: E402

DB = r"D:\neuro-memory\data\delete_crud_test.db"
if os.path.exists(DB):
    os.remove(DB)
conn = db.connect(DB)
ev = store.remember(conn, "小明在公园被狗吓到了", speaker="用户")[0]
pid = persona.find(conn, "小明")["id"]
conn.execute("INSERT INTO concepts(id,text,abstraction_level,confidence,created_at) "
             "VALUES('c_del','测试概念',1,0.8,'2026-11-12')")
conn.execute("INSERT INTO concept_links(concept_id,event_id,type) VALUES('c_del',?,'derived_from')",
             (ev,))
conn.commit()
api.conn = lambda: db.connect(DB)

print("== 概念删除 ==")
print(" before:", len(api.concepts()), "concept_links:",
      conn.execute("SELECT COUNT(*) FROM concept_links").fetchone()[0])
r = api.delete_concept("c_del")
print(" delete:", r)
assert r["ok"] is True
assert len(api.concepts()) == 0
assert conn.execute("SELECT COUNT(*) FROM concept_links").fetchone()[0] == 0
assert api.delete_concept("c_nope")["ok"] is False

print("\n== 人物删除：有绑定需 force ==")
r = api.delete_persona(pid)
print(" no-force:", r)
assert r["ok"] is False and r.get("need_force") is True

print("\n== force 删除：解除绑定并清关系 ==")
api.add_relation(api.RelationIn(a="小明", b="用户", type="朋友", strength=0.5))
r = api.delete_persona(pid, force=1)
print(" force:", r)
assert r["ok"] is True and r["unbound"] >= 1
assert persona.find(conn, "小明") is None
assert conn.execute("SELECT COUNT(*) FROM edges WHERE target=?", (pid,)).fetchone()[0] == 0
assert conn.execute("SELECT COUNT(*) FROM relations WHERE a=? OR b=?", (pid, pid)).fetchone()[0] == 0
# 事件本身保留
assert conn.execute("SELECT COUNT(*) FROM events WHERE id=?", (ev,)).fetchone()[0] == 1

print("\n== 无绑定人物直接删 ==")
p2 = persona.ensure(conn, "路人甲")
r = api.delete_persona(p2["id"])
print(" ->", r)
assert r["ok"] is True
assert api.delete_persona("p_nope")["ok"] is False

conn.close()
os.remove(DB)
print("\nPASS")