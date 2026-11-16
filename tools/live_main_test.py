"""真实主库对话测试驱动：以「用户」身份直接和 CompanionAgent 多轮对话。

- 目标库：data/neuromem.db（用户已清空的干净主库），本脚本会真实写入记忆。
- 全部 LLM 调用（agent 与 Brain 内部 cortex/reflection）统一走带 429 退避的包装，
  避免云模型限流直接中断测试。
- 轨迹写 data/main_live.trace.jsonl，报告写 data/main_live_report.json。
"""
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import neuromem.llm as _llm
from agent.companion import CompanionAgent
from agent.report import format_report

# ---- 全局 429 退避：同时覆盖 agent 与 Brain 内部调用 ----
_orig = _llm.generate


def _bounded(prompt, **kw):
    last = None
    for attempt in range(8):
        try:
            return _orig(prompt, **kw)
        except _llm.LLMError as e:
            s = str(e)
            if "429" in s:
                wait = min(90.0, 3 * (2 ** attempt) + random.random())
                print("   [429 限流·退避 %.1fs·第%d次]" % (wait, attempt + 1),
                      flush=True)
                time.sleep(wait)
                last = e
                continue
            raise
    raise last


_llm.generate = _bounded

# ---- 用户剧本（我=人类用户，agent=阿澈）----
SCRIPT = [
    "嗨，在吗？",
    "我姓周，叫周野，你以后叫我小野就行。",
    "我养了只猫，叫团子，是只小黑猫，特别黏人。",
    "你平时喜欢做什么呀？",
    "跟你说，上周三我加班到凌晨两点才回家，团子一直在门口等我。",
    "我特别讨厌吃香菜，每次点外卖都得备注不要香菜。",
    "最近工作好累，项目 deadline 压得我喘不过气。",
    "你是猫派还是狗派？",
    "对了，团子其实是橘猫啦，我之前说错了，它是橘色不是黑色。",
    "你还记得我猫叫什么名字吗？",
    "我是个怎样的人呀？",
    "好啦，我得去睡了，明天还要早起。",
    "晚安，阿澈。",
]

DB = str(ROOT / "data" / "neuromem.db")
TRACE = str(ROOT / "data" / "main_live.trace.jsonl")
REPORT = str(ROOT / "data" / "main_live_report.json")


def main():
    a = CompanionAgent(db_path=DB, use_brain=True, temperature=0.7,
                        trace_path=TRACE, session_id="main_live")
    print("=== 主库真实对话测试（干净主库，共 %d 轮）===" % len(SCRIPT))
    for t, say in enumerate(SCRIPT, 1):
        print("\n[%02d] 用户：%s" % (t, say), flush=True)
        row = a.step(say)
        print("     阿澈：%s" % row["reply"], flush=True)
        flags = []
        if row["used_ids"]:
            flags.append("采用=%s" % ",".join(map(str, row["used_ids"][:3])))
        if row["remembered_ids"]:
            flags.append("写入=%d" % len(row["remembered_ids"]))
        if row.get("fact_violation"):
            flags.append("事实收敛")
        if row.get("remember_fallback"):
            flags.append("兜底提取")
        if not row["parse_ok"]:
            flags.append("解析降级")
        if row.get("llm_error"):
            flags.append("LLM错误=%s" % row["llm_error"][:40])
        print("     [%s] 候选%d 情绪%s" % (" · ".join(flags) or "—",
              row["candidate_count"], row["mood_top"] or "-"), flush=True)
        time.sleep(1.0)  # 摊薄云调用频率
    a.save_trace()
    rep = a.report()
    Path(REPORT).write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print("\n" + format_report(rep))
    print("\n轨迹：%s" % TRACE)
    print("报告：%s" % REPORT)


if __name__ == "__main__":
    main()
