"""补录旧事实时的取代方向验收：真的走 store.remember。

背景：取代链原来的隐含假设是「后写的更新」。但补录很常见——用户 11 月才说
「清明回老家扫墓」，讲的是 4 月的事。主库 203 条里 21 条的 occurred_at 与
created_at 不同日，最极端差 7 个月。若按写入顺序判新旧，补录一句更早的单值
事实（住址/工作/婚姻）就会把当前值顶掉，而且没有任何报错。

本脚本构造「先现在、再补录、再现在」的序列，验证：

  1 我搬到上海了          → 建，活跃
  2 2025年3月我搬到杭州   → 补录旧事：**它**被作废，指向 1；1 继续活跃
  3 我搬到成都了          → 正常推进：1 被作废，指向 3；3 活跃

判据看方向，不看文本相似度。

用法：
    NEUROMEM_DB=data/backdated_verify.db python tools/verify_backdated.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import db, factkey, store  # noqa: E402


def say(conn, text, tries=3):
    for i in range(tries):
        try:
            return store.remember(conn, text)
        except Exception as e:
            if i == tries - 1:
                print("   ! 写入失败 %s -> %s" % (text, e))
                return []
            time.sleep(3 * (i + 1))
    return []


def row(conn, eid):
    return conn.execute(
        "SELECT id,gist,occurred_at,created_at,fact_key,superseded_by "
        "FROM events WHERE id=?", (eid,)).fetchone()


def main():
    conn = db.connect()
    print("db:", conn.execute("PRAGMA database_list").fetchone()[2])
    ids = []
    ok = True

    print("\n[1] 我搬到上海了")
    got = say(conn, "我搬到上海了")
    ids.append(got[0] if got else None)
    r = row(conn, ids[-1]) if ids[-1] else None
    if not r:
        print("   FAIL 未写入"); return 1
    k = factkey.loads(r["fact_key"])
    print("   id=%s occ=%s key=%s" % (r["id"], (r["occurred_at"] or "-")[:10],
                                      factkey.describe(k)))
    if not k or not k.get("f"):
        print("   FAIL 没抽成单值键，本场景无意义"); return 1

    print("\n[2] 2025年3月我搬到杭州   （补录旧事，写入时刻更晚）")
    got = say(conn, "2025年3月我搬到杭州")
    ids.append(got[0] if got else None)
    r2 = row(conn, ids[-1]) if ids[-1] else None
    if not r2:
        print("   FAIL 未写入（可能被判重复）"); ok = False
    else:
        k2 = factkey.loads(r2["fact_key"])
        occ2 = (r2["occurred_at"] or "")[:10]
        cre2 = (r2["created_at"] or "")[:10]
        print("   id=%s occ=%s cre=%s key=%s" % (r2["id"], occ2 or "-", cre2 or "-",
                                                 factkey.describe(k2)))
        r1 = row(conn, ids[0])
        good = (r2["superseded_by"] == ids[0]) and (not r1["superseded_by"])
        ok = ok and good
        print(("   PASS " if good else "   FAIL ")
              + "补录条被作废(指向第1条)=%s，第1条仍活跃=%s"
              % (r2["superseded_by"] or "-", not r1["superseded_by"]))
        if not occ2:
            print("   注意：抽取未给出 occurred_at，方向判断只能按写入顺序——"
                  "这是 timeparse 的覆盖问题，不是取代逻辑的问题")

    print("\n[3] 我搬到成都了   （当下的事，应正常推进）")
    got = say(conn, "我搬到成都了")
    ids.append(got[0] if got else None)
    r3 = row(conn, ids[-1]) if ids[-1] else None
    if not r3:
        print("   FAIL 未写入"); ok = False
    else:
        r1 = row(conn, ids[0])
        good = r1["superseded_by"] == ids[-1] and not r3["superseded_by"]
        ok = ok and good
        print(("   PASS " if good else "   FAIL ")
              + "第1条(上海)被第3条取代=%s，第3条(成都)活跃=%s"
              % (r1["superseded_by"] or "-", not r3["superseded_by"]))

    print("\n--- 库里该属性的全部版本（含已作废）---")
    for r in conn.execute(
            "SELECT gist,occurred_at,superseded_by FROM events "
            "WHERE fact_key LIKE '%\"居住地\"%' ORDER BY created_at"):
        print("   %-26s occ=%-12s sup=%s"
              % (r["gist"][:26], (r["occurred_at"] or "-")[:10], r["superseded_by"] or "-"))

    print("\nRESULT:", "PASS" if ok else "FAIL")
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
