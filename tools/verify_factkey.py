"""结构化事实键的写入路径验收：真的走 store.remember，看取代链结果。

存量 202 条语料里几乎没有状态变更（全是喜好/经历描述），所以新逻辑的
价值在存量上验不出来，必须构造针对性序列：

  1 我住在北京      → 建
  2 我搬到上海了    → 应取代 1（单值属性变更，余弦低也要认）
  3 我喜欢猫        → 建
  4 我喜欢狗        → 不应取代 3（多值属性，猫和狗可以并存）
  5 我妈住在杭州    → 建
  6 我妈搬到成都了  → 应取代 5（主体不同不能串：不能顶掉 1/2 的链）
  7 我在腾讯上班    → 建
  8 我跳槽到字节了  → 应取代 7
  9 我养了只猫叫团子 → 建
 10 我养了只狗叫团子 → 不应取代 9

用法：
    NEUROMEM_DB=data/factkey_verify.db python tools/verify_factkey.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import db, factkey, store  # noqa: E402

SCRIPT = [
    ("我住在北京", None),
    ("我搬到上海了", 0),
    ("我喜欢猫", None),
    ("我喜欢狗", None),
    ("我妈住在杭州", None),
    ("我妈搬到成都了", 4),
    ("我在腾讯上班", None),
    ("我跳槽到字节了", 6),
    ("我养了只猫叫团子", None),
    ("我养了只狗叫团子", None),
]


def main():
    conn = db.connect()
    print("db:", conn.execute("PRAGMA database_list").fetchone()[2])
    ids = []
    for idx, (text, expect_supersede_of) in enumerate(SCRIPT):
        for attempt in range(3):
            try:
                got = store.remember(conn, text)
                break
            except Exception as e:
                if attempt == 2:
                    print("[%d] FAILED %s -> %s" % (idx + 1, text, e))
                    got = []
                else:
                    time.sleep(3 * (attempt + 1))
        ids.append(got[0] if got else None)
        eid = ids[-1]
        row = conn.execute(
            "SELECT gist,fact_key,superseded_by FROM events WHERE id=?",
            (eid,)).fetchone() if eid else None
        print("[%02d] %s" % (idx + 1, text))
        if row:
            k = factkey.loads(row["fact_key"])
            print("     id=%s key=%s superseded_by=%s"
                  % (eid, factkey.describe(k), row["superseded_by"] or "-"))
        else:
            print("     （未写入新事件：可能被判为重复）")

    print("\n--- 取代链核对 ---")
    ok = True
    for idx, (text, expect) in enumerate(SCRIPT):
        if expect is None:
            continue
        old_id, new_id = ids[expect], ids[idx]
        row = conn.execute("SELECT superseded_by FROM events WHERE id=?",
                           (old_id,)).fetchone() if old_id else None
        got = row["superseded_by"] if row else None
        good = (got == new_id)
        ok = ok and good
        print(("PASS " if good else "FAIL ") + "第%d条 <<%s>> 被第%d条 <<%s>> 取代（实际=%s）"
              % (expect + 1, SCRIPT[expect][0], idx + 1, text, got))

    print("\n--- 多值属性必须并存 ---")
    for i, j in ((2, 3), (8, 9)):
        a, b = ids[i], ids[j]
        if not a or not b:
            print("SKIP  %d/%d 有事件未写入" % (i + 1, j + 1))
            continue
        rows = conn.execute(
            "SELECT id,superseded_by FROM events WHERE id IN (?,?)", (a, b)).fetchall()
        alive = [r["id"] for r in rows if not r["superseded_by"]]
        good = len(alive) == 2
        ok = ok and good
        print(("PASS " if good else "FAIL ") + "<<%s>> 与 <<%s>> 都活跃（存活 %d/2）"
              % (SCRIPT[i][0], SCRIPT[j][0], len(alive)))

    print("\nRESULT:", "PASS" if ok else "FAIL")
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
