"""用量统计：成功/失败调用都记录，按模型与按天聚合。"""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8000"


def req(path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=180) as f:
        return json.loads(f.read().decode("utf-8"))


orig = req("/api/settings")["settings"]
req("/api/usage", "DELETE")
print("清空用量")

try:
    print("\n== 1. 本地 Ollama 成功调用 ==")
    req("/api/settings", "PUT", {"llm": {"provider": "ollama",
                                        "base_url": "http://127.0.0.1:11434",
                                        "extract_model": "qwen3.5:4b",
                                        "answer_model": "qwen3:8b"}})
    print(" llm :", req("/api/settings/test", "POST", {"role": "llm"}))
    print(" emb :", req("/api/settings/test", "POST", {"role": "embed"}))

    print("\n== 2. 指向不存在的端口，制造失败 ==")
    req("/api/settings", "PUT", {"llm": {"provider": "openai_compat",
                                        "base_url": "http://127.0.0.1:9",
                                        "extract_model": "x", "answer_model": "x"}})
    r = req("/api/settings/test", "POST", {"role": "llm"})
    print(" llm :", str(r)[:140])
    assert r["ok"] is False
finally:
    req("/api/settings", "PUT", {"llm": orig["llm"], "embed": orig["embed"]})
    cur = req("/api/settings")["settings"]["llm"]
    print("\n配置已恢复:", cur["provider"], cur["answer_model"], cur["api_key_info"])

u = req("/api/usage?days=7")
t = u["totals"]
print("\nTOTALS:", t)
print("BY_MODEL:", json.dumps(u["by_model"], ensure_ascii=False))
print("RECENT[0]:", json.dumps(u["recent"][0], ensure_ascii=False))
print("BY_DAY:", u["by_day"][-1])

assert t["calls"] == 3, "应记录 3 次"
assert t["ok"] == 2 and t["failed"] == 1, "成功/失败计数不对"
assert t["tokens"] > 0
assert t["today_calls"] == 3 and t["today_tokens"] > 0
assert u["by_day"][-1]["calls"] == 3
assert len(u["by_model"]) == 3, "应有 3 组模型统计"
assert u["recent"][0]["ok"] is False and u["recent"][0]["error"]
assert abs(t["fail_rate"] - 33.3) < 0.2

print("\nPASS")