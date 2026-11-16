"""测试人-人关系抽取。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem import db, persona
from neuromem.store import remember

DB = Path(__file__).resolve().parent.parent / "data" / "test_relations.db"


def main():
    if DB.exists():
        DB.unlink()
    conn = db.connect(str(DB))
    for s in [
        "小红是小明的同事。",
        "小明的爸爸叫大明。",
        "小明喜欢小红。",
        "王哥是小明的领导。",
    ]:
        remember(conn, s)

    print("=== 人物 ===")
    for p in persona.list_personas(conn):
        print("  ", p["name"])

    print("\n=== 关系 ===")
    rels = persona.list_relations(conn)
    if not rels:
        print("  （无）")
    for r in rels:
        print("  %s --%s--> %s" % (r["a"], r["type"], r["b"]))
    conn.close()


if __name__ == "__main__":
    main()