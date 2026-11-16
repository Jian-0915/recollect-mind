"""空库下 API 自检：所有端点必须 200 且返回空结构。"""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8000"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        body = r.read().decode("utf-8")
        return r.status, body


cases = ["/api/overview", "/api/events", "/api/concepts", "/api/personas",
         "/api/relations", "/api/graph", "/api/growth", "/api/meta", "/"]
for p in cases:
    status, body = get(p)
    print(f"{p:18s} {status}  {len(body)}B")
    assert status == 200, p

o = json.loads(get("/api/overview")[1])
print("\noverview:", {k: o[k] for k in ("active_events", "archived_events", "concepts",
                                       "personas", "relations", "stage", "level", "emotion")})
assert isinstance(o["active_events"], int) and o["active_events"] >= 0
assert o["stage"] in ("空白", "感知期", "情节期", "概念期", "社会期", "自我期", "成熟期")
assert o["emotion"] and "label" in o["emotion"], "情感状态未初始化"

g = json.loads(get("/api/graph")[1])
assert isinstance(g["nodes"], list) and isinstance(g["links"], list)
print("graph:", g)
print("growth:", json.loads(get("/api/growth")[1]))
print("meta:", json.loads(get("/api/meta")[1]))
print("\n空库 API 全部 200，PASS")