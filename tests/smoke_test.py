"""冒烟测试：验证 记 -> 问 -> 召回 全链路。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem import db
from neuromem.recall import recall
from neuromem.store import remember

DB = Path(__file__).resolve().parent.parent / "data" / "smoke.db"


def main():
    if DB.exists():
        DB.unlink()
    conn = db.connect(str(DB))

    texts = [
        "小明昨天下午在公园被狗吓到了，他很害怕。",
        "今天上午小明又在小区门口被一只大狗追，差点摔倒。",
        "小红昨天在公司拿到了offer，特别开心。",
    ]
    for t in texts:
        ids = remember(conn, t)
        print("写入:", ids)

    print("\n=== 查询：小明害怕什么？===")
    for it in recall(conn, "小明害怕什么？"):
        print("  [%s] %.3f  %s" % (it["kind"], it["score"], it["gist"]))

    print("\n=== 查询：谁拿到了offer？===")
    for it in recall(conn, "谁拿到了offer？"):
        print("  [%s] %.3f  %s" % (it["kind"], it["score"], it["gist"]))

    print("\n=== 统计 ===")
    for t in ["events", "fragments", "bindings", "personas", "edges"]:
        n = conn.execute("SELECT COUNT(*) c FROM %s" % t).fetchone()["c"]
        print("  %-10s: %d" % (t, n))


if __name__ == "__main__":
    main()