"""测试成长评测 + 定时代谢。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem import db, evaluation, sleep
from neuromem.store import remember

DB = Path(__file__).resolve().parent.parent / "data" / "test_eval.db"


def main():
    if DB.exists():
        DB.unlink()
    conn = db.connect(str(DB))
    for s in [
        "小明昨天在公园被狗吓到了。",
        "小红是小明的同事。",
        "小明喜欢草莓和火锅。",
        "小明今天在公司加班。",
        "小红拿到了offer。",
    ]:
        remember(conn, s)

    print("=== 代谢前评测 ===")
    print(evaluation.evaluate(conn))

    print("\n=== sleep 跑 1 轮 ===")
    sleep.run(conn, interval=0, cycles=1)

    print("\n=== 代谢后评测 ===")
    print(evaluation.evaluate(conn))
    conn.close()


if __name__ == "__main__":
    main()