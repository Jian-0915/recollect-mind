"""诊断关系抽取的原始 LLM 输出。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem import config, llm
from neuromem.persona import _REL_PROMPT

TEXTS = [
    "小红是小明的同事。",
    "小明的爸爸叫大明。",
    "王哥是小明的领导。",
]


def main():
    for t in TEXTS:
        p = _REL_PROMPT.replace("{text}", t)
        print("---", t)
        try:
            raw = llm.generate(p, model=config.ANSWER_MODEL, max_tokens=200, timeout=60)
            print("RAW:", repr(raw[:300]))
        except Exception as e:
            print("ERR:", e)


if __name__ == "__main__":
    main()