"""测试人物档案：画像生成 + 别名合并。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem import db, persona
from neuromem.store import remember

DB = Path(__file__).resolve().parent.parent / "data" / "test_persona.db"


def main():
    if DB.exists():
        DB.unlink()
    conn = db.connect(str(DB))
    for s in [
        "小明特别怕狗。",
        "小明喜欢吃草莓和火锅。",
        "小明和小红是同事。",
        "小明昨天在公园被狗吓到，很害怕。",
        "小红今天拿到了offer，特别开心。",
        "小红喜欢喝咖啡。",
        "老王今天来公司开会。",
        "王哥说下周要出差。",
    ]:
        remember(conn, s)

    print("=== 人物列表 ===")
    for p in persona.list_personas(conn):
        print("  %s | aliases=%s | profile=%s" % (p["name"], p["aliases"], p["profile"] or "(空)"))

    print("\n=== 生成画像：小明 ===")
    print(persona.build_profile(conn, "小明"))

    print("\n=== 合并：老王 <- 王哥 ===")
    print("合并结果:", persona.merge(conn, "老王", "王哥"))

    print("\n=== 合并后人物列表 ===")
    for p in persona.list_personas(conn):
        print("  %s | aliases=%s" % (p["name"], p["aliases"]))
    conn.close()


if __name__ == "__main__":
    main()