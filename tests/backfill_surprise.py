"""回填历史事件的 surprise（与更早事件的最大相似度的补数）。

一次性维护脚本，可重复运行。用法：
    python tests/backfill_surprise.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import db, store  # noqa: E402


def main():
    c = db.connect()
    try:
        rows = c.execute("SELECT id,created_at,gist,embedding FROM events "
                         "WHERE embedding IS NOT NULL ORDER BY created_at, rowid").fetchall()
        prev = []
        for r in rows:
            emb = db.unpack(r["embedding"])
            s = 1.0 if not prev else 1.0 - max(store._cos(emb, p) for p in prev)
            s = round(max(0.0, min(1.0, s)), 3)
            c.execute("UPDATE events SET surprise=? WHERE id=?", (s, r["id"]))
            prev.append(emb)
            print(" %s surprise=%.3f  %s" % (r["id"], s, (r["gist"] or "")[:30]))
        c.commit()
        print("回填完成：%d 条" % len(rows))
    finally:
        c.close()


if __name__ == "__main__":
    main()