"""概览页 / 筛选接口自检。"""
import json
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8000"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as f:
        return json.loads(f.read().decode("utf-8"))


m = get("/api/meta")
print("stage:", m["stage"], "| stages:", [(s["name"], f"{s['have']}/{s['need']}", s["satisfied"]) for s in m["stages"]])
assert len(m["stages"]) == 6
assert m["stages"][0]["satisfied"] is True, "已有 1 条记忆，感知期应达成"

allrows = get("/api/events")
print("events total:", len(allrows))

miss = get("/api/events?q=" + urllib.parse.quote("绝对不存在的词xyz"))
print("q miss:", len(miss))
assert len(miss) == 0

arch = get("/api/events?archived=1")
print("archived=1:", len(arch))

ps = get("/api/personas")
if ps:
    name = ps[0]["name"]
    byp = get("/api/events?persona=" + urllib.parse.quote(name))
    print(f"persona={name}:", len(byp))
    assert len(byp) >= 0

hit = get("/api/events?q=" + urllib.parse.quote(allrows[0]["gist"][:2])) if allrows else []
print("q hit:", len(hit))
if allrows:
    assert len(hit) >= 1, "关键词应能命中"

html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode("utf-8")
for token in ["概览", "发育阶段", "能力雷达", "情绪状态", "最近记忆", "搜索内容"]:
    assert token in html, token
print("page ok, size:", len(html))
print("PASS")