"""验证写接口的中文存储 + 对话接口。"""
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8000"


def req(path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=300) as f:
        return json.loads(f.read().decode("utf-8"))


text = "今天下午和小红在咖啡馆聊了旅行计划，她说想去云南"
r = req("/api/events", "POST", {"text": text, "speaker": "用户"})
print("ADD:", r)
eid = r["ids"][0]
rows = req("/api/events")
row = [x for x in rows if x["id"] == eid][0]
print("GIST:", row["gist"], "| PLACE:", row["place"], "| TIME:", row["occurred_at"])
assert "小红" in row["gist"] and "云南" in row["gist"], "中文或抽取异常"
print("中文存储 OK")

r = req(f"/api/events/{eid}", "PUT", {"gist": "和小红在咖啡馆聊云南旅行计划（人工修正）", "place": "咖啡馆"})
rows = req("/api/events")
row = [x for x in rows if x["id"] == eid][0]
print("EDIT:", r, "|", row["gist"], "|", row["place"])
assert row["gist"].endswith("（人工修正）") and row["place"] == "咖啡馆"

print("ARCHIVE:", req(f"/api/events/{eid}/archive?archived=1", "POST", {}))
rows = req("/api/events")
print("archived flag:", [x for x in rows if x["id"] == eid][0]["archived"])

print("DELETE:", req(f"/api/events/{eid}", "DELETE"))
rows = req("/api/events")
print("after delete count:", len([x for x in rows if x["id"] == eid]))

t0 = time.time()
ans = req("/api/ask", "POST", {"query": "我和小明去过哪里？", "user_name": "我"})
dt = time.time() - t0
print(f"\nASK ({dt:.1f}s) hits={len(ans.get('events') or [])}")
print("SUMMARY:", (ans.get("summary") or "")[:300])
for e in (ans.get("events") or [])[:3]:
    print("  -", e.get("gist", "")[:50], e.get("confidence"), round(e.get("score", 0), 3))

html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode("utf-8")
print("\nHTML size:", len(html), "| has 对话:", "对话" in html, "| has 合并人物:", "合并人物" in html)
print("ALL PASS")