#!/usr/bin/env python
"""延迟分解：一轮对话 19.4s 到底花在哪。

现状（2026-11-16 真实 38 轮）：中位 19.4s、P90 33.6s、最长 44.6s。
作为「陪伴」场景这个延迟是体验杀手，但优化前必须先知道占比——
凭直觉去优化模型或换 provider 通常白费。

本脚本包住三个环节分别计时，不修改生产代码：
  embed   本地 ollama 向量化（每轮 1~2 次：query 一次，写入时事件一次）
  recall  brain.turn 全流程（含 embed、图扩展、评分、组装）
  llm     每次 provider 调用（抽取+回答是一跳；护栏重写会多一跳）

用法：
    python tools/probe_latency.py
    python tools/probe_latency.py --query "我最近在忙什么" --rounds 3
"""

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

MAIN_DB = BASE_DIR / "data" / "neuromem.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default="我最近在忙什么")
    ap.add_argument("--rounds", type=int, default=2)
    args = ap.parse_args()

    tmpdir = tempfile.mkdtemp(prefix="nm_lat_")
    db = Path(tmpdir) / "lat.db"
    shutil.copy2(MAIN_DB, db)

    from agent.companion import CompanionAgent

    agent = CompanionAgent(db_path=db, session_id="latency_probe", trace_path=None)

    spans = {"embed": [], "turn": [], "llm": [], "prompts": []}

    # ---- embed ----
    from neuromem import embed as embed_mod
    orig_embed = embed_mod.embed

    def timed_embed(*a, **k):
        t = time.time()
        try:
            return orig_embed(*a, **k)
        finally:
            spans["embed"].append(time.time() - t)

    embed_mod.embed = timed_embed

    # ---- brain.turn（含召回全流程）----
    orig_turn = agent.brain.turn

    def timed_turn(*a, **k):
        t = time.time()
        try:
            return orig_turn(*a, **k)
        finally:
            spans["turn"].append(time.time() - t)

    agent.brain.turn = timed_turn

    # ---- provider 调用 ----
    orig_gen = agent._generate

    def timed_gen(prompt):
        t = time.time()
        try:
            return orig_gen(prompt)
        finally:
            spans["llm"].append(time.time() - t)
            spans["prompts"].append(len(prompt or ""))

    agent._generate = timed_gen

    print("查询：%s（%d 轮）\n" % (args.query, args.rounds))
    total_start = time.time()
    replies = []
    for i in range(args.rounds):
        n_embed0, n_llm0 = len(spans["embed"]), len(spans["llm"])
        t0 = time.time()
        agent.step(args.query if i == 0 else "那我该怎么开始？")
        wall = time.time() - t0
        replies.append(dict(
            wall=wall,
            embeds=spans["embed"][n_embed0:],
            llms=spans["llm"][n_llm0:],
        ))
        print("第 %d 轮 总 %.2fs" % (i + 1, wall))
        if replies[-1]["embeds"]:
            print("   embed %d 次：%s" % (
                len(replies[-1]["embeds"]),
                "  ".join("%.2fs" % x for x in replies[-1]["embeds"])))
        if replies[-1]["llms"]:
            print("   LLM   %d 次：%s" % (
                len(replies[-1]["llms"]),
                "  ".join("%.2fs" % x for x in replies[-1]["llms"])))
        print()

    wall_total = time.time() - total_start
    print("=" * 60)
    emb_sum = sum(spans["embed"])
    llm_sum = sum(spans["llm"])
    turn_sum = sum(spans["turn"])
    print("合计 %.1fs" % wall_total)
    print("  embed  %5.1fs  %4.0f%%" % (emb_sum, 100 * emb_sum / wall_total))
    print("  recall %5.1fs  %4.0f%%  （brain.turn，含 embed）" % (
        turn_sum, 100 * turn_sum / wall_total))
    print("  LLM    %5.1fs  %4.0f%%  （%d 次调用）" % (
        llm_sum, 100 * llm_sum / wall_total, len(spans["llm"])))
    other = wall_total - turn_sum - llm_sum
    print("  其他   %5.1fs  %4.0f%%  （提示词装配 / 护栏 / 落库）" % (
        other, 100 * other / wall_total))
    if spans["prompts"]:
        avg = sum(spans["prompts"]) / len(spans["prompts"])
        print("\n提示词长度：平均 %d 字符，最长 %d" % (avg, max(spans["prompts"])))
    print("\n首条回复：%s" % str(replies[0]["wall"])[:6] if replies else "")

    agent.brain.close()
    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
