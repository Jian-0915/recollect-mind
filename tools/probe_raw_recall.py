"""原话层召回可行性探针：不写事件层，直接对 conversation_turns 原文做向量检索。

背景：现在「记不记得」完全取决于写入时模型有没有把这句话收进 remember。
没写进 events 的那 29%（78/270 条）并不是消失了——它们仍在 conversation_turns
里，但召回 pipeline 从来只查 events，所以永远想不起来。

这个探针回答一个问题：**如果把原话层也纳入召回，能不能把漏掉的那部分找回来？**

用法：
    python tools/probe_raw_recall.py            # 默认 8 个探针问题
    python tools/probe_raw_recall.py --topk 5
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

DB = BASE / "data" / "neuromem.db"

# (问题, 期望命中的原话片段)  —— 全部取自「只存在于原话、未写入事件层」的句子
PROBES = [
    ("我们组空降的经理是谁", "空降了个新经理叫陈昊"),
    ("我闺蜜叫什么名字", "有个闺蜜叫阿岚"),
    ("我最讨厌开什么会", "越来越讨厌开需求评审会"),
    ("我们代码评审严不严", "代码评审挺严的"),
    ("金毛今天撞我腿了吗", "金毛今天撞我的腿"),
    ("上个月我搬到哪儿了", "上个月我搬到了城西"),
    ("我为什么疼", "我摔倒了"),
    ("我最近在学什么", "在学篆刻"),
]


def _norm(s):
    return re.sub(r"[\s，。！？、,.!?：:；;\"'（）()\[\]【】…—-]", "", s or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()

    from neuromem import embed

    c = sqlite3.connect(args.db)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT id,ts,text FROM conversation_turns WHERE speaker='你' ORDER BY ts")]
    print("原话层：%d 条发言，开始嵌入（首次约 1~2 分钟）" % len(rows))
    vecs = []
    for i, r in enumerate(rows, 1):
        vecs.append((r["text"], embed.embed(r["text"])))
        if i % 60 == 0:
            print("  已嵌入 %d/%d" % (i, len(rows)))

    def cos(a, b):
        d = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** .5
        nb = sum(y * y for y in b) ** .5
        return d / (na * nb) if na and nb else 0.0

    hit1 = hit3 = hitk = 0
    print("\n%-24s %-6s %-6s %s" % ("问题", "rank", "分数", "命中原话"))
    for q, want in PROBES:
        qv = embed.embed(q)
        scored = sorted(((cos(qv, v), t) for t, v in vecs), reverse=True)
        rank = None
        for i, (s, t) in enumerate(scored, 1):
            if _norm(want) in _norm(t):
                rank, hit_s, hit_t = i, s, t
                break
        hit1 += (rank == 1)
        hit3 += (rank is not None and rank <= 3)
        hitk += (rank is not None and rank <= args.topk)
        print("%-24s %-6s %-6s %s" % (q, rank or "-",
                                      ("%.3f" % hit_s) if rank else "-",
                                      (hit_t or "")[:34]))
    n = len(PROBES)
    print("\n原话层召回：top1 %d/%d  top3 %d/%d  top%d %d/%d"
          % (hit1, n, hit3, n, args.topk, hitk, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
