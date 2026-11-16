"""清掉测试数据，跑几次真实调用，然后检查页面元素。"""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8000"


def rq(path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=120) as f:
        return json.loads(f.read().decode("utf-8"))


rq("/api/usage", "DELETE")
print("清空测试数据")
for i in range(2):
    print("llm#%d:" % (i + 1), rq("/api/settings/test", "POST", {"role": "llm"}).get("ok"))
print("emb   :", rq("/api/settings/test", "POST", {"role": "embed"}).get("ok"))

u = rq("/api/usage?days=7")
print("totals:", u["totals"])
print("models:", [(m["model"], m["calls"], m["tokens"]) for m in u["by_model"]])

html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode("utf-8")
for tok in ['data-tab="models"', "今日调用", "按模型", "最近调用", "modelsChart", "清空用量"]:
    print(f"  {tok!r}: {tok in html}")
print("page bytes:", len(html))