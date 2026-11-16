"""测试统一 query 接口：结构化事件 + 总结。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem import db
from neuromem.query import query
from neuromem.store import remember

DB = Path(__file__).resolve().parent.parent / "data" / "test_query.db"


def main():
    if DB.exists():
        DB.unlink()
    conn = db.connect(str(DB))
    for s in [
        "用户喜欢吃草莓。",
        "用户昨天在公园被狗吓到了。",
        "用户上周三拿到了offer。",
    ]:
        remember(conn, s, speaker="用户")

    for q in ["用户喜欢吃什么？", "昨天发生了什么？"]:
        r = query(conn, q, user_name="用户")
        print("=" * 40)
        print("查询:", q)
        print("【结构化事件】")
        if not r["events"]:
            print("  （无）")
        for it in r["events"]:
            print("  [%s] %s | 时间:%s | 情绪:%s"
                  % (it["kind"], it["gist"], it.get("occurred_at") or it.get("time_text"),
                     (it.get("affect") or {}).get("label", "")))
        print("【总结】")
        print(" ", r["summary"])
    conn.close()


if __name__ == "__main__":
    main()