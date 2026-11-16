"""Key 处理：清洗隐形字符、留空不改、掩码不改、新值覆盖。"""
import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import settings  # noqa: E402

BASE = "http://127.0.0.1:8000"


def req(path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=60) as f:
        return json.loads(f.read().decode("utf-8"))


def info():
    return req("/api/settings")["settings"]["llm"]["api_key_info"]


orig = settings.current()["llm"]["api_key"]
oinfo = settings.key_info(orig)
print("原始 Key:", oinfo)

try:
    print("\n== 1. 带空格/零宽字符的 Key 应被清洗 ==")
    raw = "  sk-test\u200bkey-1234\n"
    req("/api/settings", "PUT", {"llm": {"api_key": raw}})
    i = info()
    print("  ->", i, "| 期望 len =", len("sk-testkey-1234"))
    assert i["tail"] == "****1234" and i["len"] == len("sk-testkey-1234"), "未清洗或未保存"

    print("\n== 2. 空字符串 = 不修改 ==")
    req("/api/settings", "PUT", {"llm": {"api_key": ""}})
    i2 = info()
    print("  ->", i2)
    assert i2["tail"] == "****1234", "空值把 Key 清掉了"

    print("\n== 3. 掩码回传 = 不修改 ==")
    req("/api/settings", "PUT", {"llm": {"api_key": "****1234"}})
    i3 = info()
    print("  ->", i3)
    assert i3["tail"] == "****1234"

    print("\n== 4. 新 Key 覆盖 ==")
    req("/api/settings", "PUT", {"llm": {"api_key": "sk-another-5678"}})
    i4 = info()
    print("  ->", i4)
    assert i4["tail"] == "****5678", "新 Key 没覆盖"
finally:
    body = {"llm": {"api_key": orig}} if orig else {"clear_key": "llm"}
    req("/api/settings", "PUT", body)
    back = info()
    print("\n已恢复:", back)
    assert back["tail"] == oinfo["tail"], "恢复失败"

print("PASS")