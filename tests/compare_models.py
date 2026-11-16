"""对比 4B / 8B 在事件抽取上的质量与速度。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem import llm
from neuromem.store import _EXTRACT_PROMPT

SAMPLES = [
    "小明昨天在公园被狗吓到，很害怕。",
    "今天小明在小巷又被狗追，吓坏了。",
    "小明上周被邻居的狗咬伤了。",
    "小红今天拿到offer，非常开心。",
    "小红升职了，也很高兴。",
    "我最近总失眠，白天没精神。",
]


def run(model):
    print("=" * 18, model, "=" * 18)
    total = 0.0
    for s in SAMPLES:
        p = _EXTRACT_PROMPT.replace("{text}", s)
        t0 = time.time()
        try:
            out = llm.generate(p, model=model, json_mode=True, max_tokens=256, timeout=90)
            dt = time.time() - t0
            total += dt
            print("[%.1fs] %s" % (dt, s))
            print("      -> %s" % out.strip().replace("\n", " "))
        except Exception as e:
            print("[ERR] %s -> %s" % (s, e))
    print("--- 总耗时(含冷启动): %.1fs ---\n" % total)


if __name__ == "__main__":
    for m in ["qwen3.5:4b", "qwen3:8b"]:
        run(m)