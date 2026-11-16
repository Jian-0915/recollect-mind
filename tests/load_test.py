"""数据量压测：独立库注入 N 条事件，测写入 / 召回 / 图谱 / 概览延迟。

用法：
    python tests/load_test.py --rows 2000 --repeat 5

- 不调用 LLM（写入直接 SQL），embedding 用随机单位向量
- 不碰正式库：默认写到 data/load_test.db，跑完自动删除（--keep 保留）
"""
import argparse
import json
import os
import random
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import api, db, graph, recall  # noqa: E402

DIM = 1024
TOPICS = ["加班", "朋友", "家人", "身体", "学习", "生活", "情绪", "意外", "成就", "邻里"]
WHO = ["阿哲", "小林", "小周", "妈妈", "爸爸", "王姨", "奶奶", "表哥", "老李", "阿May"]


def build(path, rows):
    p = Path(path)
    if p.exists():
        p.unlink()
    c = db.connect(path)
    rnd = random.Random(42)
    now = datetime.now()

    pids = []
    for i, name in enumerate(WHO):
        pid = "p_load_%02d" % i
        c.execute("INSERT INTO personas(id,canonical_name,aliases_json,profile) VALUES(?,?,?,?)",
                  (pid, name, json.dumps([name], ensure_ascii=False), ""))
        pids.append(pid)

    cids = []
    for i in range(20):
        cid = "con_load_%02d" % i
        vec = [rnd.gauss(0, 1) for _ in range(DIM)]
        n = sum(v * v for v in vec) ** 0.5 or 1.0
        c.execute("INSERT INTO concepts(id,text,abstraction_level,confidence,created_at,embedding) "
                  "VALUES(?,?,?,?,?,?)",
                  (cid, "关于%s的长期认识" % TOPICS[i % len(TOPICS)], 1 + i % 2, 0.6,
                   now.isoformat(timespec="seconds"), db.pack([v / n for v in vec])))
        cids.append(cid)

    batch = []
    for i in range(rows):
        vec = [rnd.gauss(0, 1) for _ in range(DIM)]
        n = sum(v * v for v in vec) ** 0.5 or 1.0
        vec = [v / n for v in vec]
        eid = "evt_load_%06d" % i
        topic = TOPICS[i % len(TOPICS)]
        who = WHO[i % len(WHO)]
        gist = "%s和%s在%s的事情第%d次" % (who, who, topic, i)
        days = rnd.randint(0, 180)
        occ = (now - timedelta(days=days)).strftime("%Y-%m-%d")
        created = (now - timedelta(days=days // 2)).isoformat(timespec="seconds")
        batch.append((eid, created, occ, gist, db.pack(vec), rnd.random(),
                      "p_load_%02d" % (i % len(pids)), cids[i % len(cids)], i % 5 == 0))

    c.executemany(
        "INSERT INTO events(id,created_at,occurred_at,gist,embedding,salience,activation,"
        "archived,confidence,provenance,consolidation_stage,surprise) "
        "VALUES(?,?,?,?,?,?,1.0,0,1.0,'load','raw',0.5)",
        [(b[0], b[1], b[2], b[3], b[4], b[5]) for b in batch])
    c.executemany("INSERT INTO edges(source,target,type,weight) VALUES(?,?,?,1.0)",
                  [(b[0], b[6], "involves") for b in batch])
    c.executemany("INSERT INTO concept_links(concept_id,event_id,type) VALUES(?,?,?)",
                  [(b[7], b[0], "derived_from") for b in batch if b[8]])
    c.commit()
    size = p.stat().st_size
    c.close()
    return size


def ms(fn, repeat):
    out = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    return statistics.median(out), max(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1000)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    path = str(Path(__file__).resolve().parent.parent / "data" / "load_test.db")
    t0 = time.perf_counter()
    size = build(path, a.rows)
    build_s = time.perf_counter() - t0

    c = db.connect(path)
    api.conn = lambda: db.connect(path)
    recall.embed.embed = lambda q, *args, **k: [0.01] * DIM

    queries = ["最近加班多吗", "阿哲和小林是什么关系", "妈妈最近怎么样",
               "上周发生了什么", "我昨天做了什么"]
    idx = {"i": 0}

    def do_recall():
        q = queries[idx["i"] % len(queries)]
        idx["i"] += 1
        recall.recall(c, q)

    r_med, r_max = ms(do_recall, a.repeat)
    g_med, g_max = ms(lambda: graph.build_graph(c), a.repeat)
    o_med, o_max = ms(lambda: api.overview(), a.repeat)
    e_med, e_max = ms(lambda: api.events(limit=300), a.repeat)
    m_med, m_max = ms(lambda: api.meta_info(), a.repeat)

    print("rows=%d | db=%.1f MB | 注入 %.2fs" % (a.rows, size / 1024 / 1024, build_s))
    print("  recall   p50 %7.1f ms  max %7.1f ms" % (r_med, r_max))
    print("  graph    p50 %7.1f ms  max %7.1f ms" % (g_med, g_max))
    print("  overview p50 %7.1f ms  max %7.1f ms" % (o_med, o_max))
    print("  events   p50 %7.1f ms  max %7.1f ms" % (e_med, e_max))
    print("  meta     p50 %7.1f ms  max %7.1f ms" % (m_med, m_max))

    c.close()
    if not a.keep:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass


if __name__ == "__main__":
    main()