"""ask 韧性：嵌入服务挂掉时降级为关键词检索，不返回 500。"""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8000"


def req(path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=180) as f:
            return f.status, json.loads(f.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


good = req("/api/settings")[1]["settings"]["embed"]
orig_llm = req("/api/settings")[1]["settings"]["llm"]
orig_provider = orig_llm["provider"]
print("原 embed 配置:", good)
print("原 llm provider:", orig_provider, "|", orig_llm.get("base_url"))

try:
    # 1) 故意把嵌入指到没有 embeddings 接口的服务
    bad = {"provider": "openai_compat", "base_url": "https://api.deepseek.com",
           "api_key": "", "model": "bge-large:latest", "dim": 1024}
    status, r = req("/api/settings", "PUT", {"embed": bad})
    print("PUT bad embed:", status, r.get("warnings") if isinstance(r, dict) else r)
    assert status == 200

    # 2) ask 不应 500
    status, r = req("/api/ask", "POST", {"query": "小明去哪里了", "user_name": "用户"})
    print("ASK(bad embed):", status, "warnings:", r.get("warnings"))
    assert status == 200, "嵌入失败导致 500"
    assert r["warnings"] and "嵌入服务不可用" in r["warnings"][0]

    # 3) 连解释模型也挂掉时仍不 500
    status, r = req("/api/settings", "PUT",
                    {"llm": {"provider": "openai_compat",
                             "base_url": "https://api.deepseek.com",
                             "api_key": "", "extract_model": "x", "answer_model": "y"}})
    print("PUT bad llm:", status)
    status, r = req("/api/ask", "POST", {"query": "小明去哪里了", "user_name": "用户"})
    print("ASK(bad llm+embed):", status, "warnings:", r.get("warnings"))
    assert status == 200
finally:
    req("/api/settings", "PUT", {"llm": orig_llm, "embed": good})
    cur = req("/api/settings")[1]["settings"]
    print("已恢复:", cur["llm"]["provider"], "/", cur["embed"]["provider"])
    assert cur["llm"]["provider"] == orig_provider, \
        "配置未恢复：原 %s，现 %s" % (orig_provider, cur["llm"]["provider"])

status, r = req("/api/ask", "POST", {"query": "小明去哪里了", "user_name": "用户"})
print("ASK(正常配置):", status, "warnings:", r.get("warnings"))
assert status == 200 and not r.get("warnings")

print("PASS（嵌入/回答任一故障都不再 500）")