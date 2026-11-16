"""端到端：通过 API 写入一句"两人同句但无关系"的记忆，确认不会新增关系。"""
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


before = req("/api/relations")
print("BEFORE:", before)

add = req("/api/events", "POST", {"text": "小红升职了，小明为她高兴", "speaker": "用户"})
print("ADD:", add)

after = req("/api/relations")
print("AFTER :", after)
assert len(after) == len(before), "同句共现被编造成了关系"

for eid in add.get("ids", []):
    req(f"/api/events/{eid}", "DELETE")
print("CLEANUP ids:", add.get("ids"))
print("RESTORED:", req("/api/relations") == before)
print("PASS")