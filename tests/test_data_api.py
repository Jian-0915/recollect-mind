"""导出/重置接口自检（只读导出，不真正清库）。"""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8000"


def req(path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=120) as f:
        return json.loads(f.read().decode("utf-8"))


d = req("/api/export")
print("keys:", sorted(d.keys()))
print("events:", len(d["events"]), "| personas:", len(d["personas"]),
      "| concepts:", len(d["concepts"]), "| settings.provider:",
      d["settings"]["llm"]["provider"])
assert "events" in d and "settings" in d and d.get("note")
if d["events"]:
    assert "embedding" not in d["events"][0], "导出不应包含向量"
key = d["settings"]["llm"]["api_key"]
assert key == "" or key.startswith("****"), "导出不应包含明文 Key"

bad = req("/api/reset", "POST", {"confirm": "nope"})
print("reset(confirm 错):", bad)
assert bad["ok"] is False and "RESET" in bad["error"]

html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode("utf-8")
for tok in ['data-tab="data"', "危险区", "导出 JSON", "resetConfirm", "清空数据"]:
    assert tok in html, tok
for tok in ["renderConceptsTab", "openPersona", "编辑别名", "自我叙事"]:
    assert tok in html, tok
print("page bytes:", len(html))
print("PASS")