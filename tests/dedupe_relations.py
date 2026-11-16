"""关系去重与规范化：同一对人的重复关系只保留强度最高的一条。

用法：
    python tests/dedupe_relations.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import db, persona  # noqa: E402


def main():
    c = db.connect()
    rows = c.execute("SELECT rowid AS rid, * FROM relations ORDER BY rowid").fetchall()
    groups = {}
    for r in rows:
        key = tuple(sorted([r["a"], r["b"]]))
        groups.setdefault(key, []).append(r)

    removed = 0
    for key, items in groups.items():
        best = max(items, key=lambda x: (float(x["strength"] or 0), -x["rid"]))
        norm = persona.normalize_type(best["rel_type"])
        c.execute("UPDATE relations SET rel_type=? WHERE id=?", (norm, best["id"]))
        for x in items:
            if x["id"] == best["id"]:
                continue
            c.execute("DELETE FROM relations WHERE id=?", (x["id"],))
            removed += 1
            print("  删除重复：%s — %s — %s" % (x["a"], x["rel_type"], x["b"]))
    c.commit()

    left = c.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    print("关系去重完成：删除 %d 条，剩余 %d 条" % (removed, left))
    for r in c.execute("SELECT r.rel_type, pa.canonical_name a, pb.canonical_name b, r.strength "
                       "FROM relations r LEFT JOIN personas pa ON pa.id=r.a "
                       "LEFT JOIN personas pb ON pb.id=r.b"):
        print("  %s — %s — %s (%.2f)" % (r["a"], r["rel_type"], r["b"], r["strength"] or 0))
    c.close()


if __name__ == "__main__":
    main()