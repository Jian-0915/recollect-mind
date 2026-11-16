"""测试四条检索通道：关键词 / 时间 / 人物 / 语义。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem import db
from neuromem.recall import recall
from neuromem.store import remember

DB = Path(__file__).resolve().parent.parent / "data" / "test_channels.db"


def main():
    if DB.exists():
        DB.unlink()
    conn = db.connect(str(DB))
    for s in [
        "用户喜欢吃草莓。",
        "用户昨天在公园被狗吓到了。",
        "用户上周三拿到了offer。",
        "小明今天去杭州出差。",
    ]:
        remember(conn, s, speaker="用户")

    print("已写入 4 条：")
    for r in conn.execute("SELECT gist,time_text,occurred_at FROM events"):
        print("   %s | %s | %s" % (r["gist"], r["time_text"], r["occurred_at"]))

    for q in ["草莓", "昨天", "上周", "用户喜欢吃什么", "小明"]:
        print("\n查询: %s" % q)
        for it in recall(conn, q):
            print("   [%s] %.3f (kw=%.2f) %s | date=%s"
                  % (it["kind"], it["score"], it["kw"], it["gist"], it.get("occurred_at")))
    conn.close()


if __name__ == "__main__":
    main()