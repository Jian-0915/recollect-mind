"""概念/人物/自我叙事 详情接口（独立库，避免污染正式数据）。"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import api, db, persona, store  # noqa: E402

DB = r"D:\neuro-memory\data\detail_module_test.db"
if os.path.exists(DB):
    os.remove(DB)
conn = db.connect(DB)

# 造 2 条事件 + 1 个概念 + 概念来源
e1 = store.remember(conn, "小明在公园被狗吓到了", speaker="用户")[0]
e2 = store.remember(conn, "小明在小巷被狗追了", speaker="用户")[0]
pid = persona.find(conn, "小明")["id"]
conn.execute("INSERT INTO concepts(id,text,abstraction_level,confidence,created_at) "
             "VALUES(?,?,?,?,?)", ("c_test", "与狗的接触让我感到恐惧并受伤", 1, 0.82, "2026-11-11"))
conn.execute("INSERT INTO concept_links(concept_id,event_id,type) VALUES(?,?,?)",
             ("c_test", e1, "derived_from"))
conn.execute("INSERT INTO concept_links(concept_id,event_id,type) VALUES(?,?,?)",
             ("c_test", e2, "derived_from"))
conn.commit()

api.conn = lambda: db.connect(DB)

print("== 概念详情 ==")
c = api.concept_detail("c_test")
print(" text:", c["text"], "| L", c["abstraction_level"], "| 来源", len(c["events"]))
assert c["ok"] and c["abstraction_level"] == 1
assert sorted(x["id"] for x in c["events"]) == sorted([e1, e2])

print("\n== 人物详情 ==")
p = api.persona_detail(pid)
print(" name:", p["canonical_name"], "| aliases:", p["aliases"])
print(" events:", [e["gist"] for e in p["events"]])
print(" relations:", p["relations"])
assert p["ok"] and len(p["events"]) == 2
assert p["aliases"] == ["小明"]

print("\n== 别名编辑 ==")
r = api.persona_aliases(pid, {"aliases": ["小明", "明明", "  "]})
print(" ->", r)
assert r["aliases"] == ["小明", "明明"]
assert api.persona_detail(pid)["aliases"] == ["小明", "明明"]

print("\n== 自我叙事（未生成时返回 null） ==")
s = api.self_info()
print(" ->", s)
assert s["text"] is None

print("\n== 不存在 ==")
assert api.concept_detail("c_nope")["ok"] is False
assert api.persona_detail("p_nope")["ok"] is False
assert api.persona_aliases("p_nope", {"aliases": ["x"]})["ok"] is False

conn.close()
os.remove(DB)
print("\nPASS")