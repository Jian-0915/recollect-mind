"""补抽取关系：对已有事件中"含关系词"的句子重跑一次关系抽取。

写入时 LLM 偶发失败会漏关系；这个脚本可重复运行，已抽过的会跳过（按 evidence 去重）。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import db, persona  # noqa: E402


def main():
    c = db.connect()
    seen = {r["evidence"] for r in c.execute("SELECT evidence FROM relations")}
    rows = c.execute("SELECT id,gist FROM events WHERE archived=0 ORDER BY created_at").fetchall()
    todo = [r for r in rows if persona.has_relation_hint(r["gist"] or "")
            and (r["gist"] or "") not in seen]
    print("含关系词的未抽取事件：%d / %d" % (len(todo), len(rows)))

    added = 0
    for i, r in enumerate(todo, 1):
        try:
            res = persona.extract_relations(c, r["gist"], speaker="用户")
        except Exception as e:
            print("  [%d] FAIL %s | %s" % (i, r["gist"][:22], str(e)[:80]))
            continue
        if res:
            added += len(res)
            print("  [%d] +%s | %s" % (i, res[0]["type"], r["gist"][:26]))
        else:
            print("  [%d] 无 | %s" % (i, r["gist"][:26]))

    total = c.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    print("\n新增关系 %d 条，当前共 %d 条" % (added, total))
    for r in c.execute("SELECT r.rel_type, pa.canonical_name a, pb.canonical_name b "
                       "FROM relations r LEFT JOIN personas pa ON pa.id=r.a "
                       "LEFT JOIN personas pb ON pb.id=r.b"):
        print("  %s — %s — %s" % (r["a"], r["rel_type"], r["b"]))
    c.close()


if __name__ == "__main__":
    main()