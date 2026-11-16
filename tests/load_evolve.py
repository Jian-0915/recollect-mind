"""大库巩固耗时：N 条事件下跑一轮概念归纳与遗忘，确认不会卡死。

用法：
    python tests/load_evolve.py --rows 3000
"""
import argparse
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import consolidator, db, embed, forgetting  # noqa: E402

DIM = 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=3000)
    a = ap.parse_args()

    path = str(Path(__file__).resolve().parent.parent / "data" / "evolve_load.db")
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass
    c = db.connect(path)
    rnd = random.Random(3)
    for i in range(10):
        c.execute("INSERT INTO personas(id,canonical_name,aliases_json,profile) VALUES(?,?,?,?)",
                  ("p_ev_%02d" % i, "人物%d" % i, "[]", ""))
        c.execute("INSERT INTO events(id,created_at,gist,embedding,salience,activation,archived,"
                  "confidence,provenance,consolidation_stage,surprise) "
                  "VALUES(?,?,?,?,0.5,1.0,0,1.0,'load','raw',0.5)",
                  ("evt_ev_%06d" % i, datetime.now().astimezone().isoformat(timespec="seconds"),
                   "事件%d" % i, db.pack([0.01] * DIM)))
        c.execute("INSERT INTO edges(source,target,type,weight) VALUES(?,?,?,1.0)",
                  ("evt_ev_%06d" % i, "p_ev_%02d" % (i % 10), "involves"))
    for i in range(10, a.rows):
        vec = [rnd.gauss(0, 1) for _ in range(DIM)]
        n = sum(v * v for v in vec) ** 0.5 or 1.0
        c.execute("INSERT INTO events(id,created_at,gist,embedding,salience,activation,archived,"
                  "confidence,provenance,consolidation_stage,surprise) "
                  "VALUES(?,?,?,?,0.5,1.0,0,1.0,'load','raw',0.5)",
                  ("evt_ev_%06d" % i, datetime.now().astimezone().isoformat(timespec="seconds"),
                   "第%d条关于工作和朋友的经历" % i, db.pack([v / n for v in vec])))
        c.execute("INSERT INTO edges(source,target,type,weight) VALUES(?,?,?,1.0)",
                  ("evt_ev_%06d" % i, "p_ev_%02d" % (i % 10), "involves"))
    c.commit()

    embed.embed = lambda t, *args, **k: [0.02] * DIM
    import neuromem.consolidator as cs
    cs.llm.generate = lambda *args, **k: "用户与同事在工作中常有来往"

    t0 = time.perf_counter()
    created = consolidator.run(c)
    t1 = time.perf_counter() - t0
    t2s = time.perf_counter()
    decay = forgetting.decay(c)
    t2 = time.perf_counter() - t2s

    print("rows=%d | 巩固 %.2fs（新建 %d 概念）| 遗忘 %.3fs（归档 %d）"
          % (a.rows, t1, len(created), t2, decay.get("newly_archived", 0)))

    # 连续几轮：确认 FIFO 分批能持续推进，不会卡在同一批
    for r in range(1, 4):
        t = time.perf_counter()
        n = len(consolidator.run(c))
        left = c.execute("SELECT COUNT(*) FROM events WHERE archived=0 AND id NOT IN "
                         "(SELECT event_id FROM concept_links) AND last_try_at IS NULL").fetchone()[0]
        print("  第 %d 轮：%.2fs · 新建 %d · 本库未尝试事件 %d" % (r + 1, time.perf_counter() - t, n, left))
    c.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


if __name__ == "__main__":
    main()