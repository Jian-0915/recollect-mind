"""回填并校验 v13 结构化事实键。

两件事，分开授权：

1. ``--backfill``（默认）：只写 ``events.fact_key`` 一列。不动 gist、不动
   superseded_by、不建删事件。抽不出的留 NULL，store 会自动退回文本判据。
2. ``--rebuild``：额外用结构化冲突重建取代链（会写 superseded_by）。这一步
   会改数据，务必先 ``--dry-run`` 看清单再决定。

用法：
    python tools/backfill_factkey.py --db data/factkey_verify.db --dry-run
    python tools/backfill_factkey.py --db data/factkey_verify.db --backfill --rebuild
    python tools/backfill_factkey.py            # 主库，默认只回填不重建
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import config, db, store  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="默认 config.DB_PATH（主库）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backfill", action="store_true", help="回填 fact_key（默认执行）")
    ap.add_argument("--rebuild", action="store_true", help="额外用结构化冲突重建取代链")
    ap.add_argument("--force", action="store_true", help="重算已有键（口径变更后刷一遍）")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    path = args.db or str(config.DB_PATH)
    conn = db.connect(path)
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM events WHERE fact_key IS NOT NULL").fetchone()[0]
        print("db:", path)
        print("已有 fact_key 的事件:", before)

        res = store.backfill_factkeys(conn, limit=args.limit, dry_run=args.dry_run,
                                      force=args.force)
        total, filled, skipped = res["total"], res["filled"], res["skipped"]
        rate = (filled / total * 100) if total else 0
        print("回填: total=%d filled=%d skipped=%d (抽取率 %.1f%%)%s"
              % (total, filled, skipped, rate, " [dry-run 未写]" if args.dry_run else ""))
        print("样本:")
        for s in res["samples"]:
            print("  -", s["gist"][:40])
            print("    key:", json.dumps(s["key"], ensure_ascii=False))

        if args.rebuild:
            marked = store.rebuild_factkey_conflicts(conn, dry_run=args.dry_run)
            print("\n结构化冲突取代: %d 对%s"
                  % (len(marked), " [dry-run 未写]" if args.dry_run else ""))
            for m in marked[:15]:
                print("  %s(%s=%s)  ←  %s(%s=%s)"
                      % ((m["old_gist"] or "")[:26], m["attr"], m["old_value"],
                         (m["new_gist"] or "")[:26], m["attr"], m["new_value"]))
            if len(marked) > 15:
                print("  ... 还有 %d 对" % (len(marked) - 15))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
