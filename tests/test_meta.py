"""测试元架构/阶段解锁。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem import db, evolve, meta
from neuromem.store import remember

DB = Path(__file__).resolve().parent.parent / "data" / "test_meta.db"


def main():
    if DB.exists():
        DB.unlink()
    conn = db.connect(str(DB))

    print("=== 空库阶段 ===")
    print(meta.report(conn))

    for s in [
        "小明昨天在公园被狗吓到了。",
        "小红是小明的同事。",
        "小明喜欢草莓。",
    ]:
        remember(conn, s)

    print("\n=== 记 3 条后 ===")
    r = meta.report(conn)
    print("阶段:", r["stage"], "| 已解锁:", r["unlocked"], "| 指标:", r["metrics"])

    evolve.tick(conn)
    print("\n=== 跑一轮 evolve 后 ===")
    r = meta.report(conn)
    print("阶段:", r["stage"], "| 已解锁:", r["unlocked"], "| 指标:", r["metrics"])
    conn.close()


if __name__ == "__main__":
    main()