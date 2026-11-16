"""关系抽取防幻觉：人物与关系词都必须在原文出现。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import db, persona  # noqa: E402

DB = r"D:\neuro-memory\data\rel_guard.db"
if os.path.exists(DB):
    os.remove(DB)
conn = db.connect(DB)


def pair(a, b):
    return conn.execute(
        "SELECT COUNT(*) FROM relations r "
        "JOIN personas pa ON r.a=pa.id JOIN personas pb ON r.b=pb.id "
        "WHERE (pa.canonical_name=? AND pb.canonical_name=?) "
        "   OR (pa.canonical_name=? AND pb.canonical_name=?)", (a, b, b, a)).fetchone()[0]


def rels():
    return conn.execute(
        "SELECT pa.canonical_name a, pb.canonical_name b, r.rel_type t "
        "FROM relations r JOIN personas pa ON r.a=pa.id JOIN personas pb ON r.b=pb.id").fetchall()


def run(label, text, speaker="用户"):
    out = persona.extract_relations(conn, text, speaker=speaker)
    print(f"[{label}] {text}\n    -> {out}")
    return out


print("== 1. 无关系词，直接返回 ==")
assert run("no-hint", "小红拿到了offer") == []
assert pair("小明", "小红") == 0

print("\n== 2. 句中只有小红，不得拉入小明 ==")
run("only-xiaohong", "小红升职了，同事们都很替她高兴")
assert pair("小明", "小红") == 0, "凭空把小明和小红连起来了"

print("\n== 3. 两人同句但没有关系表述，不得编造'同事' ==")
run("co-occur", "小红升职了，小明为她高兴")
row = conn.execute("SELECT COUNT(*) FROM relations WHERE rel_type='同事'").fetchone()[0]
assert row == 0, "同处一句被当成同事关系"
assert pair("小明", "小红") == 0

print("\n== 4. 明确关系，应写入 ==")
run("explicit", "小明和小红是同事")
assert pair("小明", "小红") == 1

print("\n== 5. 重复抽取不重复插入 ==")
run("dup", "小明和小红是同事")
assert pair("小明", "小红") == 1, "重复插入"

print("\n== 6. 代词按 speaker 归位 ==")
run("pronoun", "我是小明的好朋友，我们认识很久了", speaker="用户")
assert pair("用户", "小明") >= 1
assert pair("我", "小明") == 0, "'我' 被当成人物建了档"

print("\n== 7. 关系词不能当人名 ==")
n = conn.execute("SELECT COUNT(*) FROM personas WHERE canonical_name IN "
                 "('同事','朋友','老板','领导')").fetchone()[0]
assert n == 0, "关系词被建成了人物"

print("\n最终关系：")
for r in rels():
    print("  ", r["a"], "--", r["t"], "->", r["b"])
print("\nALL PASS")
conn.close()