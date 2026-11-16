"""全量重算嵌入向量（换嵌入模型的迁移工具）。

用法：
    python tools/reembed.py --dry-run          # 只看规模，不动库
    python tools/reembed.py --dim 2560         # 全量重算并校验维度

必须一次算完：新旧维度混存不会被发现，但 `_cos` 会按短维截断，结果全错。
"""
import argparse
import sys
import time

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from neuromem import db, embed, settings  # noqa: E402

TABLES = [("events", "gist"), ("concepts", "text")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dim", type=int, default=0, help="期望维度，0 表示不校验")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cfg = settings.load()
    emb = cfg.get("embed") or {}
    print("embed: %s / %s / dim=%s" % (emb.get("provider"), emb.get("model"), emb.get("dim")))
    if not a.dry_run:
        probe = embed.embed("维度探测")
        print("实际维度:", len(probe))
        if a.dim and len(probe) != a.dim:
            print("维度不符：期望 %d，实际 %d，已中止" % (a.dim, len(probe)))
            return 2

    c = db.connect()
    for table, col in TABLES:
        rows = c.execute("SELECT id, %s AS t FROM %s" % (col, table)).fetchall()
        print("%s: %d 条" % (table, len(rows)))
        if a.dry_run:
            continue

        items = [(r["id"], (r["t"] or "").strip()) for r in rows if (r["t"] or "").strip()]
        done, failed = 0, 0
        t0 = time.time()
        BATCH = 32
        for i in range(0, len(items), BATCH):
            chunk = items[i:i + BATCH]
            try:
                vecs = embed.embed_many([t for _, t in chunk])
            except Exception:
                vecs = []
                for _, t in chunk:                      # 整批失败再逐条回退
                    try:
                        vecs.append(embed.embed(t))
                    except Exception:
                        vecs.append(None)
            for (eid, _), v in zip(chunk, vecs):
                if not v:
                    failed += 1
                    print("  FAIL", eid)
                    continue
                if a.dim and len(v) != a.dim:
                    print("维度不符 %d != %d，中止" % (len(v), a.dim))
                    return 2
                c.execute("UPDATE %s SET embedding=? WHERE id=?" % table, (db.pack(v), eid))
                done += 1
            c.commit()
            if (i // BATCH + 1) % 5 == 0:
                print("  %d/%d  %.0fs" % (done, len(items), time.time() - t0))
        c.commit()
        print("  -> 更新 %d 条，失败 %d 条，用时 %.1fs" % (done, failed, time.time() - t0))

    for table, _ in TABLES:
        n = c.execute("SELECT COUNT(*) FROM %s WHERE embedding IS NOT NULL" % table).fetchone()[0]
        print("校验 %s: embedding 非空 %d" % (table, n))
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())