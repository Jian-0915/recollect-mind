"""诊断 answer 退化：打印 prompt 与 4B/8B 的原始输出。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from neuromem.answer import _PROMPT, build_memory_block

ITEMS = [
    {"kind": "verified", "gist": "我喜欢吃草莓", "affect": {}, "time_text": "", "place": ""}
]
QUESTION = "我喜欢吃什么？"


def build_prompt(user):
    return (
        _PROMPT.replace("{user}", user)
        .replace("{memory}", build_memory_block(ITEMS))
        .replace("{question}", QUESTION)
    )


def call(model, prompt):
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "num_predict": 256,
            "temperature": 0.2,
            "num_ctx": 4096,
            "repeat_penalty": 1.25,
        },
    }
    r = requests.post("http://127.0.0.1:11434/api/generate", json=body, timeout=90)
    d = r.json()
    return d.get("response", ""), d.get("eval_count")


def main():
    prompt = build_prompt("用户")
    print("===== PROMPT =====")
    print(prompt)
    print("===== PROMPT LEN:", len(prompt), "=====\n")
    for m in ["qwen3.5:4b", "qwen3:8b"]:
        try:
            out, n = call(m, prompt)
            print("---- %s (eval=%s) ----" % (m, n))
            print(repr(out[:300]))
        except Exception as e:
            print("---- %s ERROR: %s" % (m, e))


if __name__ == "__main__":
    main()