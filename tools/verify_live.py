"""闭环验收：web 陪伴通道是否真写记忆、真能下一轮召回。

判据（缺一不可）：
  1. 一轮 live.turn 后 conversation_turns +2（用户 + agent）
  2. companion_state.last_turn_at 被推进（会话时钟不再停摆）
  3. events 增加（事实真落库）
  4. 第二轮换个问法问同一件事，回复能带出第一轮讲过的事实
  5. 全程 L0 不变（identity 宪法 / persona_card 一字节不动）

用法：
    NEUROMEM_DB=data/live_verify.db python tools/verify_live.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import companion, db, identity, live, persona_card  # noqa: E402


def snap():
    c = db.connect()
    try:
        return {
            "turns": c.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0],
            "events": c.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "last_turn_at": companion.state(c).get("last_turn_at"),
            "identity": identity.block(c),
            "persona": persona_card.render(c),
        }
    finally:
        c.close()


def say(tag, text):
    t0 = time.time()
    r = live.turn(text)
    if not r.get("ok"):
        print("[%s] FAILED (%s) %s" % (tag, r.get("error"), r.get("hint") or ""))
        return r
    print("[%s] %s" % (tag, text))
    print("     → %s" % r.get("reply"))
    print("     · 记忆写入 %d 条 / 引用 %d 条 / 情绪 %s / %.1fs"
          % (r.get("remembered_count") or 0, r.get("used") or 0,
             r.get("mood") or "-", time.time() - t0))
    print("     · hint: %s" % (r.get("hint") or "-"))
    for f in (r.get("remembered") or []):
        print("       + %s" % f)
    return r


def main():
    print("db:", live.status().get("db"))
    b = snap()
    print("BEFORE turns=%d events=%d last_turn_at=%s"
          % (b["turns"], b["events"], b["last_turn_at"]))

    r1 = say("R1", "我上周去了青岛，吃了鲅鱼饺子，还去栈桥看了海")
    if not r1.get("ok"):
        print("RESULT: FAIL (第一轮没跑通，后续判据无意义)")
        return 1

    m = snap()
    print("AFTER1 turns=%d (%+d) events=%d (%+d) last_turn_at=%s"
          % (m["turns"], m["turns"] - b["turns"], m["events"],
             m["events"] - b["events"], m["last_turn_at"]))

    r2 = say("R2", "我上周去哪了？吃的什么？")
    reply2 = str(r2.get("reply") or "")

    checks = {
        "C1 会话轮次写入": m["turns"] - b["turns"] >= 2,
        "C2 会话时钟推进": m["last_turn_at"] != b["last_turn_at"],
        "C3 事实落库": m["events"] - b["events"] >= 1,
        "C4 下一轮召回": ("青岛" in reply2) or ("鲅鱼" in reply2),
        "C5 L0 未被改动": (m["identity"] == b["identity"]
                          and m["persona"] == b["persona"]),
    }
    print("\n--- 判据 ---")
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    ok = all(checks.values())
    print("\nRESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
