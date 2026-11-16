"""诊断 deepseek 配置：只打印非敏感信息。"""
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\neuro-memory")

from neuromem import settings  # noqa: E402

c = settings.current()
llm = c["llm"]
key = llm.get("api_key") or ""
print("provider:", llm["provider"])
print("base_url:", llm["base_url"])
print("models:", llm["extract_model"], "/", llm["answer_model"])
print("key: len=%d head=%s tail=%s" % (len(key), key[:3] + "***" if key else "-",
                                       "***" + key[-2:] if len(key) > 2 else "-"))

url = settings.openai_base(llm["base_url"]) + "/chat/completions"
for model in [llm["answer_model"], "deepseek-chat"]:
    body = {"model": model, "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5, "stream": False}
    r = requests.post(url, json=body, headers=settings.openai_headers(llm), timeout=30)
    print(f"\nPOST {url} model={model} -> {r.status_code}")
    print("  body:", r.text[:400])