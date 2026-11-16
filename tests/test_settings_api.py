"""配置相关 HTTP 接口自检。"""
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


s = req("/api/settings")
print("provider:", s["settings"]["llm"]["provider"], "/", s["settings"]["embed"]["provider"])
print("presets:", list((s["presets"] or {}).keys()))
assert "ollama" in s["presets"] and "deepseek" in s["presets"]

h = req("/api/health")
print("health:", h)
assert h["provider"] == "ollama" and h["embed_dim"] == 1024

# 掩码回传不应覆盖真实 key
before = s["settings"]["llm"]["api_key"]
r = req("/api/settings", "PUT", {"llm": {"api_key": "****abcd"}})
print("PUT masked ->", r["settings"]["llm"]["api_key"], "warnings:", r["warnings"])
assert r["ok"] and r["settings"]["llm"]["api_key"] == before
req("/api/settings", "PUT", {"llm": {"api_key": before}})

t = req("/api/settings/test", "POST", {"role": "llm"})
print("test llm:", t)
assert t["ok"] and t["reply"]

te = req("/api/settings/test", "POST", {"role": "embed"})
print("test embed:", te)
assert te["ok"] and te["dim"] == 1024

m = req("/api/settings/models?provider=ollama")
print("models:", (m.get("models") or [])[:6])
assert m["ok"]

html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode("utf-8")
assert 'data-tab="models"' in html and "保存并立即生效" in html
print("page has settings tab, size:", len(html))
print("PASS")