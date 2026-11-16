"""自动进化：状态 / 最小事件短路 / 手动触发 / 阈值分支 / 并发锁。"""
import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "http://127.0.0.1:8000"


def req(path, method="GET"):
    r = urllib.request.Request(BASE + path, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=180) as f:
        return json.loads(f.read().decode("utf-8"))


st = req("/api/evolve/status")
print("STATUS:", json.dumps(st, ensure_ascii=False))
assert st["enabled"] is True
assert st["interval"] > 0 and st["threshold"] >= 1 and st["min_events"] >= 1
assert "events_since" in st and "running" in st

print("\n== 1. 事件不足时自动跳过 ==")
r = req("/api/evolve?force=false", "POST")
print(" ->", r)
assert r["ok"] is True and r.get("skipped") is True, "应跳过（当前事件数少于门槛）"

print("\n== 2. 手动强制跑一轮 ==")
r = req("/api/evolve", "POST")
print(" ->", json.dumps(r, ensure_ascii=False))
assert r["ok"] is True and r.get("skipped") is False
for k in ("new_concepts", "abstracted", "archived", "events", "concepts"):
    assert k in r, k

st2 = req("/api/evolve/status")
print("\nSTATUS after:", json.dumps({k: st2[k] for k in ("runs", "last_run", "next_run", "last_result")},
                                   ensure_ascii=False))
assert st2["runs"] >= 1 and st2["last_run"]

print("\n== 3. 阈值触发分支（monkeypatch） ==")
from neuromem import autoevolve as ae  # noqa: E402

ae.events_since_last = lambda: 5
ae._active_events = lambda: 5
ae._seconds_since_last = lambda: 999
assert ae.maybe_trigger() is True, "增量达阈值应触发"
time.sleep(1.0)

print("\n== 4. 并发锁：进行中直接拒绝 ==")
ae._lock.acquire()
try:
    r = ae.run_once(force=True)
    print(" ->", r)
    assert r["ok"] is False and "进行中" in r["error"]
finally:
    ae._lock.release()

print("\n== 5. 关闭开关 ==")
os.environ["NEUROMEM_AUTOEVOLVE"] = "0"
assert ae.enabled() is False
os.environ.pop("NEUROMEM_AUTOEVOLVE")
assert ae.enabled() is True

html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode("utf-8")
for tok in ["进化一次", "evolveText", "evolveNow", "/api/evolve/status"]:
    assert tok in html, tok
print("page ok:", len(html))
print("PASS")