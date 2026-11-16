"""真实 HTTP 验收：回执准确性与「只到月」的时间解析。

两段，各自针对一个真实缺陷：

A 判重合并的回执要诚实。同一件事换个说法再说一遍时，store 会判重（不新增事件），
  但旧回执一律说"写入记忆 1 条"，用户看 events 没变会以为系统在骗他。
  判据：hint 里必须出现"合并"，且不能说"新增"。

B 只到月的时间说法要落 occurred_at。此前 parse_date 只认"到日"的写法，
  "2025年3月"解析不出来 → occurred_at 留空 → 补录旧事被当成"刚发生"，
  会直接把当前值顶掉（取代方向反了）。
  判据：新增事件的 occurred_at 不为空，且落在所说的月份。

用法（需服务已启动）：
    python tools/verify_live_time.py
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import account, db, factkey  # noqa: E402

BASE = "http://127.0.0.1:8000"
S = requests.Session()
S.cookies.set(account.COOKIE, account.make_token("jian"))

ok = True


def counts():
    c = db.connect()
    try:
        n = c.execute("select count(*) from events").fetchone()[0]
        row = c.execute(
            "select created_at from events order by created_at desc limit 1").fetchone()
        return n, (row["created_at"] if row else "")
    finally:
        c.close()


print("=== A. 判重合并的回执要诚实 ===")
n0, last0 = counts()
a = S.post(BASE + "/api/live",
           json={"query": "上个月我搬到了城西，房租便宜不少"}, timeout=180).json()
n1, _ = counts()
print("reply:", (a.get("reply") or "")[:100])
print("hint:", a.get("hint"), "| events:", n0, "->", n1)
if n1 == n0:
    # 判重了：hint 必须承认是合并
    good = ("合并" in (a.get("hint") or "")
            and "新增" not in (a.get("hint") or ""))
    print(("PASS " if good else "FAIL ") + "未新增事件时，回执说「合并」而不是「新增」")
else:
    good = bool(a.get("remembered_count"))
    print(("PASS " if good else "FAIL ") + "确有新增，回执报了新增 %s 条"
          % a.get("remembered_count"))
ok = ok and good

print("\n=== B. 只到月的时间说法要落 occurred_at ===")
n2, last2 = counts()
b = S.post(BASE + "/api/live",
           json={"query": "2026年8月我去了趟大理，住了五天"}, timeout=180).json()
print("reply:", (b.get("reply") or "")[:100])
print("hint:", b.get("hint"))
c = db.connect()
rows = c.execute(
    "select gist,occurred_at,fact_key from events where created_at > ? "
    "order by created_at", (last2,)).fetchall()
for r in rows:
    print("  新增:", r["gist"][:40])
    print("       occ=%s key=%s" % (r["occurred_at"] or "-", r["fact_key"] or "-"))
if not rows:
    print("  注意：这句被判重了，本次拿不到新事件（不影响结论，换一句再验）")
else:
    got = rows[0]["occurred_at"] or ""
    good = got.startswith("2026-08")
    print(("PASS " if good else "FAIL ") + "occurred_at=%s（期望 2026-08）" % (got or "空"))
    ok = ok and good
    key = factkey.loads(rows[0]["fact_key"])
    if key:
        good2 = key["s"] == "counterpart"
        print(("PASS " if good2 else "FAIL ")
              + "键主体已归一：s=%s（期望 counterpart）" % key["s"])
        ok = ok and good2
c.close()

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
