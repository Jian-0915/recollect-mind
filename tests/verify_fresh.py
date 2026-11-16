"""初始化后自检：空库各表为 0，阶段回退到起点。"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\neuro-memory")

from neuromem import db, evaluation, meta  # noqa: E402

c = db.connect()
tables = ["events", "fragments", "bindings", "personas", "edges", "relations",
          "concepts", "concept_links", "growth_log"]
print("== 表行数 ==")
for t in tables:
    n = c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
    print(f"  {t:15s} {n}")
    assert n == 0, f"{t} 不是空表"

print("\n== 阶段 ==")
mr = meta.report(c)
print(" stage:", mr["stage"], "| level:", mr["level"], "| unlocked:", mr["unlocked"])
assert mr["level"] == -1 and mr["stage"] == "空白", "阶段没有回到起点"

print("\n== 评分 ==")
ev = evaluation.evaluate(c)
print(" ", ev)
assert ev["score"] == 0, "评分没有归零"

print("\n空库结构 OK，可开始从零写入")
c.close()