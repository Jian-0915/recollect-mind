"""原话层召回方案对比实验：查询改写 × 邻近窗口 × 混合检索。

问题背景
--------
「记不记得」现在取决于写入时模型有没有把话收进 remember。没进 events 的
那部分（实测 78/270 = 29%）并没有消失，它们还在 conversation_turns 里，
但召回只会查 events，所以永远想不起来。

上一轮探针证明了「把原话层加进来」有用，也暴露了硬伤：「我为什么疼」的
答案「我摔倒了。流血了」在原话层只排第 7 —— 纯语义相似度连不上「疼」和
「摔倒」。这是共指问题，不是向量问题。

这个实验回答四个问题：
  Q1 查询改写：把最近几轮用户发言拼进 query，共指类问题能救回多少？
  Q2 邻近窗口：命中一条后带出前后 N 条，收益 / 成本（注入字数）比如何？
  Q3 混合检索：向量 + 2gram 混合（RRF 融合），比纯向量强多少？
  Q4 每轮多一次 embed，延迟代价是多少？

注意：本库只有 2 个 session 且都是长连续对话，所以「对话段」这个检索单元
不成立（一段就是整个库）。改用「turn 序列上的邻近窗口」，这是可实现的。

用法：
    python tools/experiment_episode_recall.py             # 用向量缓存
    python tools/experiment_episode_recall.py --rebuild   # 强制重算缓存
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

DB = BASE / "data" / "neuromem.db"
CACHE = BASE / "data" / "_episode_vecs.json"

# 评测集：(问题, 期望内容, 问这句话之前用户说了什么)
# ctx 取自真实会话里紧邻的前几轮，用来模拟"带上下文的查询改写"
PROBES = [
    ("我们组空降的经理是谁", "空降了个新经理叫陈昊", []),
    ("我闺蜜叫什么名字", "有个闺蜜叫阿岚", []),
    ("我最讨厌开什么会", "越来越讨厌开需求评审会", []),
    ("我们代码评审严不严", "代码评审挺严的", []),
    ("金毛今天撞我腿了吗", "金毛今天撞我的腿", []),
    ("上个月我搬到哪儿了", "上个月我搬到了城西", []),
    # 共指难点：问句和答案没有共享实词，全靠上下文才能连上
    ("我为什么疼", "我摔倒了", ["今天心情好差啊", "我摔倒了。流血了"]),
    ("我最近在学什么", "在学篆刻", []),
]

_TAG = re.compile(r"\[id:[^\]]+\]")
_WS = re.compile(r"[\s，。！？、,.!?：:；;\"'（）()\[\]【】…—-]+")
_USER = ("你", "user", "counterpart")


def _norm(s):
    return _WS.sub("", _TAG.sub("", s or ""))


def _bigrams(s):
    s = _norm(s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _cos(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    d = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** .5
    nb = sum(y * y for y in b) ** .5
    return d / (na * nb) if na and nb else 0.0


def _kw_score(q, t):
    """2gram 覆盖率：问句的词在候选里出现了几成。纯词法，零成本。"""
    qb = _bigrams(q)
    if not qb:
        return 0.0
    return len(qb & _bigrams(t)) / len(qb)


def _rrf(rank_lists, k=60):
    """Reciprocal Rank Fusion：只用名次，免疫各通道分数尺度不同的问题。"""
    score = {}
    for ranks in rank_lists:
        for pos, idx in enumerate(ranks, 1):
            score[idx] = score.get(idx, 0.0) + 1.0 / (k + pos)
    return sorted(score.items(), key=lambda x: -x[1])


def _embed_cached(texts, rebuild=False):
    cache = {}
    if CACHE.exists() and not rebuild:
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    todo = [t for t in dict.fromkeys(texts) if t not in cache]
    if todo:
        from neuromem import embed
        print("  需要嵌入 %d 条（缓存命中 %d）" % (len(todo), len(cache)))
        for i in range(0, len(todo), 32):
            batch = todo[i:i + 32]
            for t, v in zip(batch, embed.embed_many(batch)):
                cache[t] = v
        CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    turns = [dict(r) for r in conn.execute(
        "SELECT id,session_id,ts,speaker,text FROM conversation_turns ORDER BY ts")]
    # 原话层只索引「对方说的话」——我自己的回复不是记忆
    mine = [t for t in turns if t["speaker"] in _USER and (t["text"] or "").strip()]
    print("原话 %d 条，其中对方发言 %d 条" % (len(turns), len(mine)))

    t0 = time.time()
    cache = _embed_cached([t["text"] for t in mine], args.rebuild)
    qtexts = []
    for q, _, ctx in PROBES:
        qtexts += [q, "。".join(ctx[-2:] + [q])] if ctx else [q]
    cache = _embed_cached(qtexts, args.rebuild)
    print("嵌入耗时 %.1fs（缓存命中时几乎为 0）" % (time.time() - t0))

    tv = [cache[t["text"]] for t in mine]

    def top_vec(v, k=10):
        return [i for _, i in sorted(((_cos(v, vv), i) for i, vv in enumerate(tv)),
                                     key=lambda x: -x[0])][:k]

    def top_kw(q, k=10):
        return [i for _, i in sorted(((_kw_score(q, t["text"]), i)
                                      for i, t in enumerate(mine)),
                                     key=lambda x: -x[0])][:k]

    # 每个方案：(名称, 是否带上下文, 是否混合, 邻近窗口)
    plans = [
        ("A0 单条 / 原问题",              False, False, 0),
        ("A1 单条 / 带上下文",            True,  False, 0),
        ("A2 单条 / 带上下文 + 混合",      True,  True,  0),
        ("A3 单条 / 带上下文 + 混合 + ±1",  True,  True,  1),
        ("A4 单条 / 带上下文 + 混合 + ±2",  True,  True,  2),
        ("A5 单条 / 带上下文 + 混合 + ±3",  True,  True,  3),
    ]

    rows = []
    for name, use_ctx, hybrid, win in plans:
        ok1 = ok3 = 0
        chars = []
        detail = []
        for q, want, ctx in PROBES:
            query = "。".join(ctx[-2:]) + "。" + q if (use_ctx and ctx) else q
            v = cache[query]
            vec_ranks = top_vec(v, k=min(10, len(mine)))
            if hybrid:
                kw_ranks = top_kw(q, k=min(10, len(mine)))
                fused = [i for i, _ in _rrf([vec_ranks, kw_ranks])]
            else:
                fused = vec_ranks
            # 取前 3 个命中点，各展开 ±win 条相邻发言，合成注入文本
            idxs = set()
            for i in fused[:3]:
                for d in range(-win, win + 1):
                    j = i + d
                    if 0 <= j < len(mine):
                        idxs.add(j)
            block = " ".join(mine[j]["text"] for j in sorted(idxs))
            chars.append(len(block))
            w = _norm(want)
            r = None
            # 名次定义：展开后第一个含答案的「块」排第几（按 fused 顺序）
            for pos, i in enumerate(fused[:3], 1):
                seg = " ".join(mine[j]["text"] for j in range(max(0, i - win), min(len(mine), i + win + 1)))
                if w in _norm(seg):
                    r = pos
                    break
            if r is None:
                # 没进前 3 个命中点的窗口里，看整块有没有（有但排不进 top3）
                r = 4 if w in _norm(block) else None
            ok1 += (r == 1)
            ok3 += (r is not None and r <= 3)
            detail.append((q, r))
        rows.append((name, ok1, ok3, sum(chars) / len(chars), detail))

    n = len(PROBES)
    print("\n" + "=" * 86)
    print("%-34s %-7s %-7s %-10s" % ("方案", "top1", "top3", "平均注入字数"))
    print("-" * 86)
    for name, ok1, ok3, avg, _ in rows:
        print("%-34s %d/%d     %d/%d     %.0f" % (name, ok1, n, ok3, n, avg))
    print("=" * 86)

    best = max(rows, key=lambda r: (r[1], r[2], -r[3]))
    print("\n最佳 %s 逐条：" % best[0])
    for q, r in best[4]:
        print("  %-24s %s" % (q, ("rank=%d" % r) if r else "MISS"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
