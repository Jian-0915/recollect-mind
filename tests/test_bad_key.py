"""无效 API Key 时，错误信息要能看出服务端真实原因。"""
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


cfg = req("/api/settings")[1]["settings"]
print("provider:", cfg["llm"]["provider"], "| base:", cfg["llm"]["base_url"],
      "| model:", cfg["llm"]["answer_model"])

if cfg["llm"]["provider"] == "openai_compat" and "deepseek" not in cfg["llm"]["base_url"]:
    print("当前不是 deepseek，跳过")
    sys.exit(0)

st, r = req("/api/settings/test", "POST", {"role": "llm"})
print("\nTEST:", st)
print("  ok:", r.get("ok"), "| need_key:", r.get("need_key"))
print("  error:", (r.get("error") or "")[:260])
assert st == 200
if r.get("ok"):
    print("Key 有效，无需修复提示")
    sys.exit(0)
assert r.get("need_key") is True
assert "API Key 无效" in (r.get("error") or ""), "缺少可操作的提示"
assert "Authentication" in (r.get("error") or "") or "invalid" in (r.get("error") or "").lower(), \
    "未透出服务端原因"

q = urllib.parse.urlencode({"provider": "openai_compat",
                            "base_url": cfg["llm"]["base_url"]})
st, m = req("/api/settings/models?" + q)
print("\nMODELS:", st, "| ok:", m.get("ok"), "| need_key:", m.get("need_key"))
print("  error:", (m.get("error") or "")[:260])
assert st == 200 and m.get("ok") is False and m.get("need_key") is True

print("\nPASS（错误信息含服务端原因与操作指引）")