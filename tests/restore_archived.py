"""恢复所有归档事件（归档恢复会把 activation 拉回 1.0）。"""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8000"


def rq(path, method="GET"):
    r = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(r, timeout=60) as f:
        return json.loads(f.read().decode("utf-8"))


arch = rq("/api/events?archived=1")
print("归档数量:", len(arch))
for a in arch:
    print("  恢复:", a["gist"][:30])
    rq("/api/events/%s/archive?archived=0" % a["id"], "POST")
print("现有活跃:", len(rq("/api/events?archived=0")))
print("剩余归档:", len(rq("/api/events?archived=1")))