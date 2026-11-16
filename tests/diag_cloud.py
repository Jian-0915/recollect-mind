import json
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8")

for model in ["qwen3.5:4b", "qwen3:8b"]:
    for extra in ({}, {"think": False}, {"reasoning_effort": "none"}):
        body = {"model": model,
                "messages": [{"role": "user", "content": "只回复两个字：好的"}],
                "stream": False, "max_tokens": 64, "temperature": 0.2}
        body.update(extra)
        r = requests.post("http://127.0.0.1:11434/v1/chat/completions", json=body, timeout=180)
        try:
            d = r.json()
        except Exception:
            print(model, extra, r.status_code, r.text[:200])
            continue
        msg = (d.get("choices") or [{}])[0].get("message", {})
        print(f"\n{model} extra={extra} status={r.status_code}")
        print("  content:", repr((msg.get("content") or "")[:80]))
        print("  reasoning:", repr((msg.get("reasoning") or msg.get("reasoning_content") or "")[:80]))
        print("  finish:", (d.get("choices") or [{}])[0].get("finish_reason"))