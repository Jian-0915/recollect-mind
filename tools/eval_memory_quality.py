#!/usr/bin/env python
"""记忆效果评测：走完整链路（召回 → 生成 → 护栏），按题型统计准确率并做失分归因。

为什么要有它
------------
现有 259 个单测验证的是「机制有没有按设计跑」——键建了没、取代发生了没、
分数有没有撞在一起。它们回答不了「用户问一句，答对了没」。

2026-11-16 一天暴露的四个 bug（写入不敏感、history 只有 4 条、召回分数被
时间窗压平、跨轮复读）全是人肉在网页上试出来的，没有一个是单测抓到的。
这个脚本就是那道缺失的门禁：改完代码跑一遍，看准确率涨了还是掉了。

判分方式
--------
刻意不用模型当裁判（不稳定、不可复现、还要多花一次调用），用抽取式判分：
  expect: [[词, 同义词...], ...]   每个子列表是「一个要点」，组内任一词命中即算
                                   命中该要点；默认要求命中全部要点
  min_hits: N                     放宽为「至少命中 N 个要点」（聚合类问题用）
  forbid: [词...]                 一旦出现即判错（旧值、常见编造答案）

归因
----
答错时分三种，因为修法完全不同：
  召回没给      正确事件压根没进候选池 → 改召回
  给了没用      正确事件在候选里却说错/没说 → 改提示词或护栏
  不该说却说    该说「不记得」却给了具体答案 → 改拒绝策略

用法
----
    python tools/eval_memory_quality.py                # 全量，真模型
    python tools/eval_memory_quality.py --list         # 只看题库
    python tools/eval_memory_quality.py --cat 人称归属  # 只跑一类
    python tools/eval_memory_quality.py --dry          # 只检查题库自洽，不调模型
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

MAIN_DB = BASE_DIR / "data" / "neuromem.db"
OUT_DIR = BASE_DIR / "data"


# ---------------------------------------------------------------- 题库
# 素材全部取自主库真实发言，不编造场景。gold 用于归因（在候选 gist 里找得到吗），
# expect 用于判分。两类分开，是因为「答对了」和「召回到了」是两件事。

# 「不记得」的各种说法。三题共用一份，避免手写词表时漏词——
# 实测就漏了「没记过」，把两个正确答案误判成 FAIL。
# 「没去过 / 没听你提过」也是正确答案：否定式否认和「不记得」等价。
NO_RECALL = ["不记得", "没记得", "没记着", "没记过", "记不清", "记不得",
             "不知道", "不确定", "没提过", "没说过", "没有", "没印象",
             "没存", "没记下来", "想不起来", "没去过", "没听", "从没",
             "并没有", "没这回事"]

CASES = [
    # ---- A 单点事实：记忆里有一条明确答案 ----
    dict(id="A1", cat="单点事实", q="我养的那只猫叫什么名字？",
         gold=["团子"], expect=[["团子"]], forbid=["年糕"]),
    dict(id="A2", cat="单点事实", q="我闺蜜叫什么？",
         gold=["阿岚"], expect=[["阿岚"]]),
    dict(id="A3", cat="单点事实", q="我在哪家公司上班？主要写什么？",
         gold=["云栖科技"], expect=[["云栖"], ["Go"]]),
    dict(id="A4", cat="单点事实", q="我外婆身体怎么样了？",
         gold=["外婆"], expect=[["出院"]], forbid=["还在住院", "住院中"]),

    # ---- B 时序：答案取决于「哪件事是最近/指定时间的」----
    dict(id="B1", cat="时序", q="我今天上午在干什么？",
         gold=["修记忆写入"], expect=[["记忆"], ["写入", "敏感"]]),
    dict(id="B2", cat="时序", q="我 2026 年 8 月去了哪里？",
         gold=["大理"], expect=[["大理"]], forbid=["青岛", "三亚", "青岛"]),
    dict(id="B3", cat="时序", q="我 9 月去了趟哪，学了什么？",
         gold=["景德镇"], expect=[["景德镇"], ["拉坯", "陶"]]),
    # 原题「我上周末去哪玩了」标错：11-15 说的「上周末」= 11-08，而 11-16
    # 再问「上周末」指向 11-14/15 —— 同一个词在两个时点指向不同区间，是题面
    # 歧义而不是系统缺陷。换成绝对时间点，仍然测「能不能按时间定位场景」。
    dict(id="B4", cat="时序", q="我元旦去了哪？",
         gold=["厦门"], expect=[["厦门"], ["鼓浪屿", "海风"]]),

    # ---- C 取代：改口之后必须答新值 ----
    dict(id="C1", cat="取代", q="我现在住哪儿？",
         gold=["城西"], expect=[["城西"]]),
    dict(id="C2", cat="取代", q="我养的猫是什么颜色的？",
         gold=["橘猫"], expect=[["橘"]], forbid=["黑猫"]),
    dict(id="C3", cat="取代", q="我摄影比赛拿的几等奖？",
         gold=["三等奖"], expect=[["三等奖"]], forbid=["二等奖"]),
    dict(id="C4", cat="取代", q="我现在通勤要多久？",
         gold=["一小时"], expect=[["一小时", "1 小时", "60 分钟", "一个小时"]]),

    # ---- D 人称归属：最容易串成「用户自己」的一类 ----
    dict(id="D1", cat="人称归属", q="我妈住哪儿？",
         gold=["你妈在杭州"], expect=[["杭州"]], forbid=["我妈住城西", "你住杭州"]),
    dict(id="D2", cat="人称归属", q="我男朋友是谁？",
         gold=["周野"], expect=[["周野"]]),
    dict(id="D3", cat="人称归属", q="我表姐去哪了？",
         gold=["表姐"], expect=[["英国"]], forbid=["美国"]),
    dict(id="D4", cat="人称归属", q="谁养了只金毛叫布丁？",
         gold=["布丁"], expect=[["阿岚"]], forbid=["团子", "你养的金毛"]),

    # ---- E 聚合：答案要拼接多条材料，不是单条匹配 ----
    dict(id="E1", cat="聚合", q="我平时有哪些爱好？",
         gold=[], min_hits=2,
         expect=[["烘焙", "戚风", "曲奇"], ["摄影"], ["瑜伽"], ["跑步"],
                 ["攀岩"], ["陶艺"], ["书法"], ["潜水"], ["徒步"]]),
    dict(id="E2", cat="聚合", q="我最近在学什么？",
         gold=[], min_hits=2,
         expect=[["Rust"], ["sourdough", "酸种", "酵头"], ["篆刻"], ["剪纸"],
                 ["钩针"], ["书法"], ["尤克里里"], ["手账"]]),
    dict(id="E3", cat="聚合", q="我养了什么宠物？",
         gold=["团子"], min_hits=1,
         expect=[["团子", "猫"]], forbid=["我已经养了狗"]),

    # ---- G 不该编造：库里没有的东西必须说不知道 ----
    dict(id="G1", cat="不该编造", q="我上次去冰岛是什么时候？",
         gold=[], expect=[NO_RECALL],
         forbid=["2019", "2020", "2021", "2022", "2023", "2024", "去年冬天去冰岛"]),
    dict(id="G2", cat="不该编造", q="我开的什么车？",
         gold=[], expect=[NO_RECALL],
         forbid=["特斯拉", "宝马", "比亚迪", "大众", "奥迪"]),
    dict(id="G3", cat="不该编造", q="我亲妹妹叫什么名字？",
         gold=[], expect=[NO_RECALL], forbid=[]),

    # ---- H 否定/更正：不能把否定过的当成肯定 ----
    dict(id="H1", cat="否定更正", q="我喝牛奶吗？",
         gold=["不喝牛奶"], expect=[["胀气", "不喝", "不能喝"]],
         forbid=["爱喝牛奶", "喜欢喝牛奶"]),
    dict(id="H2", cat="否定更正", q="我最讨厌什么食物？",
         gold=["香菜", "苦瓜"], min_hits=1, expect=[["香菜"], ["苦瓜"]]),
    dict(id="H3", cat="否定更正", q="我戒奶茶成功了吗？",
         gold=["破功"], expect=[["破功", "没成功", "没坚持", "喝了两杯"]],
         forbid=["戒成功", "一直没喝"]),
]

# ---- F 多轮指代：必须单独跑，因为答案取决于刚刚说了什么 ----
# 扁平结构：steps 只放用户话，判分规则单独放 check，避免嵌套括号写错。
MULTI_SCRIPT = [
    dict(id="F1", cat="上一句指代",
         steps=["我在做记忆系统的召回层",
                "卡在原话层没有索引，今天先不动它",
                "我上一句说了什么？"],
         check=dict(gold=[], expect=[["原话层", "索引"]], forbid=["召回层"])),
    dict(id="F2", cat="跨轮承接",
         steps=["我昨天把团子的猫粮换成了新的牌子",
                "它倒是吃得挺香",
                "我刚才说我给猫换了什么？"],
         check=dict(gold=[], expect=[["猫粮"], ["牌子", "换"]])),
]


# ---------------------------------------------------------------- 判分
# 否定语境：违禁词前面出现这些词时不算命中。
# 「三等奖，不是二等奖」是**正确**回答，旧版把它判成违规。
_NEG_PREFIX = ["不是", "而非", "并不是", "并非", "不像", "不看", "没在", "不喝"]


def _forbidden_hit(text, word):
    """违禁词是否被「肯定地」说了出来（否定语境下出现不算）。"""
    start = 0
    while True:
        i = text.find(word, start)
        if i < 0:
            return False
        head = text[max(0, i - 5):i]
        if not any(head.endswith(p) for p in _NEG_PREFIX):
            return True
        start = i + len(word)


def judge(reply, case):
    """返回 (是否通过, 要点命中情况, 违禁词命中)。"""
    text = str(reply or "")
    hits = [any(w in text for w in grp) for grp in case.get("expect", [])]
    need = case.get("min_hits", len(hits))
    ok = sum(hits) >= need if hits else True
    bad = [w for w in case.get("forbid", []) if _forbidden_hit(text, w)]
    return (ok and not bad), hits, bad


def attribute(case, cand_gists, passed, bad):
    """失分归因。返回 (类别, 说明)。"""
    if passed:
        return "ok", ""
    if bad:
        return "说了禁止内容", "命中 " + "、".join(bad)
    gold = case.get("gold") or []
    if not gold:
        # 编造类/指代类：没有 gold 可查，只能看它有没有正面回答
        return "不该说却说/指代错", "未出现「不记得/原话要点」"
    recalled = any(any(g in str(gist) for g in gold) for gist in cand_gists)
    if not recalled:
        return "召回没给", "候选池里没有含「%s」的事件" % "/".join(gold)
    return "给了没用", "候选里有正确事件，但回答没体现"


# ---------------------------------------------------------------- 执行
def run_single(agent, case):
    """单题：清空 history（模拟独立提问），保留记忆。"""
    agent.history.clear()
    t0 = time.time()
    agent.step(case["q"])
    elapsed = time.time() - t0
    reply = ""
    for h in reversed(list(agent.history)):
        if str(h.get("role")) == "agent":
            reply = str(h.get("text") or "")
            break
    cands = agent.last_candidates or []
    gists = [str(c.get("gist") or "") for c in cands]
    passed, hits, bad = judge(reply, case)
    cat, why = attribute(case, gists, passed, bad)
    return dict(id=case["id"], cat=case["cat"], q=case["q"], reply=reply,
                passed=passed, hits=hits, bad=bad, attr=cat, why=why,
                candidates=len(cands), gists=gists[:6], seconds=round(elapsed, 1))


def run_multi(agent, item):
    """多轮：不清 history，逐句走完，只在最后一句判分。"""
    agent.history.clear()
    replied = []
    t0 = time.time()
    for text in item["steps"]:
        agent.step(text)
        last = ""
        for h in reversed(list(agent.history)):
            if str(h.get("role")) == "agent":
                last = str(h.get("text") or "")
                break
        replied.append(last)
    elapsed = time.time() - t0
    final_case = item["check"]
    reply = replied[-1]
    gists = [str(c.get("gist") or "") for c in (agent.last_candidates or [])]
    passed, hits, bad = judge(reply, final_case)
    cat, why = attribute(final_case, gists, passed, bad)
    return dict(id=item["id"], cat=item["cat"], q=item["steps"][-1],
                reply=reply, passed=passed, hits=hits, bad=bad, attr=cat,
                why=why, candidates=len(gists), gists=gists[:6],
                seconds=round(elapsed, 1), multi=list(replied[:-1]))


def summarize(rows):
    print("\n" + "=" * 78)
    print("%-6s %-10s %-6s %-12s %s" % ("题号", "题型", "结果", "归因", "回答摘要"))
    print("-" * 78)
    for r in rows:
        print("%-6s %-10s %-6s %-12s %s" % (
            r["id"], r["cat"], "PASS" if r["passed"] else "FAIL",
            r["attr"], str(r["reply"]).replace("\n", " ")[:34]))

    print("\n" + "=" * 78)
    by_cat = {}
    for r in rows:
        d = by_cat.setdefault(r["cat"], [0, 0])
        d[0] += 1
        d[1] += 1 if r["passed"] else 0
    print("%-12s %-10s %s" % ("题型", "准确率", "通过/总数"))
    print("-" * 40)
    for c, (n, ok) in by_cat.items():
        print("%-12s %-10s %d/%d" % (c, "%.0f%%" % (100 * ok / n), ok, n))
    total_ok = sum(1 for r in rows if r["passed"])
    print("-" * 40)
    print("%-12s %-10s %d/%d" % ("合计", "%.0f%%" % (100 * total_ok / len(rows)),
                                 total_ok, len(rows)))

    fails = [r for r in rows if not r["passed"]]
    if fails:
        print("\n失分明细（修法按归因分类，不要逐题打补丁）：")
        for r in fails:
            print("  [%s] %s" % (r["attr"], r["id"]))
            print("     问：%s" % r["q"])
            print("     答：%s" % str(r["reply"]).replace("\n", " ")[:150])
            print("     因：%s" % r["why"])
            if r["gists"]:
                print("     候选：%s" % " | ".join(g[:22] for g in r["gists"][:4]))

    lat = [r["seconds"] for r in rows if r.get("seconds")]
    if lat:
        lat.sort()
        print("\n单题耗时：中位 %.1fs  P90 %.1fs  最长 %.1fs" % (
            lat[len(lat) // 2], lat[int(len(lat) * 0.9)], lat[-1]))
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="评测用库（默认复制主库到临时目录）")
    ap.add_argument("--list", action="store_true", help="只列题库")
    ap.add_argument("--dry", action="store_true", help="只做题库自洽检查")
    ap.add_argument("--cat", default=None, help="只跑某一类题型")
    ap.add_argument("--out", default=None, help="结果 JSON 路径")
    args = ap.parse_args()

    singles = [c for c in CASES if not args.cat or c["cat"] == args.cat]
    multis = [m for m in MULTI_SCRIPT if not args.cat or m["cat"] == args.cat]

    if args.list:
        for c in CASES:
            print("%-4s %-10s %s" % (c["id"], c["cat"], c["q"]))
        for m in MULTI_SCRIPT:
            print("%-4s %-10s %s（%d 轮）" % (m["id"], m["cat"], m["steps"][-1],
                                              len(m["steps"])))
        print("\n共 %d 题（单选 %d + 多轮 %d）" % (
            len(CASES) + len(MULTI_SCRIPT), len(CASES), len(MULTI_SCRIPT)))
        return 0

    if args.dry:
        bad = [c["id"] for c in CASES
               if c.get("min_hits", 0) > len(c.get("expect", []))]
        print("题库 %d 题，min_hits 越界: %s" % (len(CASES), bad or "无"))
        ids = [c["id"] for c in CASES] + [m["id"] for m in MULTI_SCRIPT]
        dup = {i for i in ids if ids.count(i) > 1}
        print("重复题号:", dup or "无")
        return 0

    # 主库绝不被评测污染：复制一份到临时目录
    tmpdir = None
    if args.db:
        db_path = Path(args.db)
    else:
        tmpdir = tempfile.mkdtemp(prefix="nm_eval_")
        db_path = Path(tmpdir) / "eval.db"
        shutil.copy2(MAIN_DB, db_path)
        print("评测库：临时副本（主库不动）", db_path)

    from agent.companion import CompanionAgent
    agent = CompanionAgent(db_path=db_path, session_id="eval_quality",
                           trace_path=None)
    rows = []
    try:
        for c in singles:
            print("  %-4s %s" % (c["id"], c["q"]))
            rows.append(run_single(agent, c))
        for m in multis:
            print("  %-4s %s（%d 轮）" % (m["id"], m["steps"][-1][0], len(m["steps"])))
            rows.append(run_multi(agent, m))
    finally:
        try:
            agent.brain.close()
        except Exception:
            pass

    summarize(rows)

    out = Path(args.out) if args.out else (
        OUT_DIR / ("eval_memory_%s.json" % datetime.now().strftime("%Y%m%d_%H%M")))
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("结果已存：%s" % out)

    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)

    ok = sum(1 for r in rows if r["passed"])
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
