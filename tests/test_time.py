"""测试相对时间解析 + 写入后的 occurred_at。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem import db, timeparse
from neuromem.store import remember

DB = Path(__file__).resolve().parent.parent / "data" / "time_test.db"


def main():
    print("=== 时间解析 ===")
    for c in ["昨天", "今天", "前天", "上周三", "本周五", "3天前", "2026-11-01", "11月5日", "明天", "上周"]:
        print("  %-12s -> date=%s | range=%s"
              % (c, timeparse.parse_date(c), timeparse.parse_range(c)))

    print("\n=== 写入验证 occurred_at ===")
    if DB.exists():
        DB.unlink()
    conn = db.connect(str(DB))
    for s in ["小明昨天在公园被狗吓到。", "小红上周三拿到了offer。", "用户3天前去了杭州。"]:
        remember(conn, s, speaker="用户")
    for r in conn.execute("SELECT gist,time_text,occurred_at FROM events"):
        print("  %s | when=%s | date=%s" % (r["gist"], r["time_text"], r["occurred_at"]))
    conn.close()


if __name__ == "__main__":
    main()