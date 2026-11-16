"""诊断 generate_json(json_mode=True) 的返回。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem import config, llm
from neuromem.persona import _REL_PROMPT

TEXT = "小红是小明的同事。"


def main():
    prompt = _REL_PROMPT.replace("{text}", TEXT)
    print("=== json_mode=True, 原始 ===")
    raw = llm.generate(prompt, model=config.ANSWER_MODEL, json_mode=True, max_tokens=200, timeout=60)
    print(repr(raw[:400]))
    print("=== generate_json 返回 ===")
    try:
        d = llm.generate_json(prompt, model=config.ANSWER_MODEL, max_tokens=200, timeout=60)
        print(type(d), repr(d)[:400])
    except Exception as e:
        print("ERR:", e)


if __name__ == "__main__":
    main()