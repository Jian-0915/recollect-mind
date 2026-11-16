"""并发稳定性压测：多线程同时读（recall/overview/events），并叠加写入。

用法：
    python tests/load_concurrency.py --rows 5000 --threads 8 --rounds 15
"""
import argparse
import os
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import api, db, recall  # noqa: E402

DIM = 1024
QUERIES = ["最近加班多吗", "阿哲最近怎么样", "上周发生了什么", "妈妈还好吗", "我昨天做了什么"]


def build(path, rows):
    p = Path(path)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass
    c = db.connect(path)
    rnd = random.Random(7)
    now = datetime.now()
    for i in range(10):
        c.execute("INSERT INTO personas(id,canonical_name,aliases_json,profile) VALUES(?,?,?,?)",
                  ("p_cc_%02d" % i, ["阿哲", "小林", "小周", "妈妈", "爸爸"][i % 5] + str(i),
                   "[]", ""))
    batch = []
    for i in range(rows):
        vec = [rnd.gauss(0, 1) for _ in range(DIM)]
        n = sum(v * v for v in vec) ** 0.5 or 1.0
        days = rnd.randint(0, 200)
        batch.append(("evt_cc_%06d" % i,
                      (now - timedelta(days=days // 2)).isoformat(timespec="seconds"),
                      (now - timedelta(days=days)).strftime("%Y-%m-%d"),
                      "第%d条关于加班和朋友的记忆" % i,
                      db.pack([v / n for v in vec]),
                      rnd.random(), "p_cc_%02d" % (i % 10)))
    c.executemany("INSERT INTO events(id,created_at,occurred_at,gist,embedding,salience,"
                  "activation,archived,confidence,provenance,consolidation_stage,surprise) "
                  "VALUES(?,?,?,?,?,?,1.0,0,1.0,'load','raw',0.5)", [b[:6] for b in batch])
    c.executemany("INSERT INTO edges(source,target,type,weight) VALUES(?,?,?,1.0)",
                  [(b[0], b[6], "involves") for b in batch])
    c.commit()
    c.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=5000)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=15)
    ap.add_argument("--writers", type=int, default=2)
    a = ap.parse_args()

    path = str(Path(__file__).resolve().parent.parent / "data" / "concurrency_test.db")
    t0 = time.perf_counter()
    build(path, a.rows)
    print("注入 %d 条：%.2fs" % (a.rows, time.perf_counter() - t0))

    api.conn = lambda: db.connect(path)
    recall.embed.embed = lambda q, *args, **k: [0.01] * DIM

    lat, errors = [], []
    lock = threading.Lock()
    idx = {"i": 0}

    def reader():
        for _ in range(a.rounds):
            with lock:
                q = QUERIES[idx["i"] % len(QUERIES)]
                idx["i"] += 1
            t = time.perf_counter()
            try:
                c = db.connect(path)
                try:
                    recall.recall(c, q)
                finally:
                    c.close()
                api.overview()
                api.events(limit=100)
                dt = (time.perf_counter() - t) * 1000
                with lock:
                    lat.append(dt)
            except Exception as e:
                with lock:
                    errors.append(str(e)[:120])

    writers_done = {"n": 0, "fail": 0}

    def writer():
        for i in range(40):
            try:
                c = db.connect(path)
                try:
                    c.execute("INSERT INTO events(id,created_at,gist,embedding,salience,"
                              "activation,archived,confidence,provenance,consolidation_stage,"
                              "surprise) VALUES(?,?,?,?,0.5,1.0,0,1.0,'load','raw',0.5)",
                              ("evt_w_%s_%03d" % (threading.get_ident(), i),
                               datetime.now().astimezone().isoformat(timespec="seconds"),
                               "并发写入的第%d条" % i, db.pack([0.01] * DIM)))
                    c.commit()
                finally:
                    c.close()
                writers_done["n"] += 1
            except Exception as e:
                writers_done["fail"] += 1
                if "locked" not in str(e).lower():
                    print("  write err:", str(e)[:100])

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=a.threads + a.writers) as ex:
        futs = [ex.submit(reader) for _ in range(a.threads)]
        futs += [ex.submit(writer) for _ in range(a.writers)]
        for f in futs:
            f.result()
    total = time.perf_counter() - t0

    n = len(lat)
    if n:
        lat.sort()
        p50 = statistics.median(lat)
        p95 = lat[min(n - 1, int(n * 0.95))]
        print("\n并发读 %d 线程 × %d 轮 = %d 次请求" % (a.threads, a.rounds, n))
        print("  总耗时 %.2fs · 吞吐 %.1f req/s" % (total, n / total))
        print("  延迟 p50 %.0f ms · p95 %.0f ms · max %.0f ms" % (p50, p95, lat[-1]))
    print("并发写 成功 %d · 失败 %d" % (writers_done["n"], writers_done["fail"]))
    print("错误数 %d" % len(errors))
    for e in errors[:3]:
        print("  ", e)

    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


if __name__ == "__main__":
    main()