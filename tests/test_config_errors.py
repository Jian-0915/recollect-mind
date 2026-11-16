"""配置错误时的可读提示：拉列表 / 测试 / 写入都不 500。"""
import json
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8000"


def req(path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=120) as f:
            return f.status, json.loads(f.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


print("== 1. 云端无 key 拉列表 ==")
q = urllib.parse.urlencode({"provider": "openai_compat",
                            "base_url": "https://api.deepseek.com", "api_key": ""})
st, r = req("/api/settings/models?" + q)
print(st, r)
assert st == 200 and r["ok"] is False and r.get("need_key") is True

print("\n== 2. 本地 ollama 拉列表 ==")
q = urllib.parse.urlencode({"provider": "ollama", "base_url": "http://127.0.0.1:11434"})
st, r = req("/api/settings/models?" + q)
print(st, (r.get("models") or [])[:4])
assert st == 200 and r["ok"] is True

orig = req("/api/settings")[1]["settings"]
try:
    print("\n== 3. 抽取模型不可用：写入仍不 500（原文兜底） ==")
    req("/api/settings", "PUT", {"llm": {
        "provider": "openai_compat", "base_url": "https://api.deepseek.com",
        "api_key": "", "extract_model": "nope", "answer_model": "nope"}})
    st, r = req("/api/events", "POST", {"text": "测试：模型不可用时的写入", "speaker": "用户"})
    print(st, r)
    assert st == 200, "写入失败导致 500"
    for eid in (r.get("ids") or []):
        req(f"/api/events/{eid}", "DELETE")
        print("  已清理测试事件:", eid)
    if r.get("error"):
        print("  返回可读错误")

    print("\n== 4. 嵌入也不可用：返回 error 而非 500 ==")
    req("/api/settings", "PUT", {"embed": {
        "provider": "openai_compat", "base_url": "https://api.deepseek.com",
        "api_key": "", "model": "nope", "dim": 1024}})
    st, r = req("/api/events", "POST", {"text": "测试：嵌入也不可用", "speaker": "用户"})
    print(st, r)
    assert st == 200 and r.get("error"), "嵌入失败应返回可读错误"
finally:
    req("/api/settings", "PUT", {"llm": orig["llm"], "embed": orig["embed"]})
    back = req("/api/settings")[1]["settings"]
    print("\n已恢复:", back["llm"]["provider"], "/", back["embed"]["provider"])

print("\n== 5. 恢复后本地可用 ==")
st, r = req("/api/settings/test", "POST", {"role": "llm"})
print(st, r)
assert st == 200 and r["ok"] is True
st, r = req("/api/settings/test", "POST", {"role": "embed"})
print(st, r)
assert st == 200 and r["ok"] is True
print("PASS")