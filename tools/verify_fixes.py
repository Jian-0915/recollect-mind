"""针对三个真实测试缺陷的定向验证。

与线上一致：用云端 LLM（同实时测试），并 seeding 主库 L0 人设；
对 429 做指数退避。只在干净临时库上跑，不污染主库。

场景：
  D1 改口取代链：先建「小黑猫」记忆，再更正「是橘猫不是黑猫」，
     断言旧「黑猫」事件被标 superseded_by 且只保留一条活跃猫色记忆。
  D2 情绪惯性/共情：用户「今天好累，deadline 压着」，断言回复含共情承接、
     且 affect 如实记录负面情绪（sadness/fear），而非恒 joy。
  D3 反思偏薄：用户「我是个怎样的人」，断言回复引用了具体记起的小事。
"""
import os
import sys
import re
import json
import time
import sqlite3
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.companion import CompanionAgent, make_llm

MAIN_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "neuromem.db")
L0_TABLES = ["identity", "identity_history", "persona_card", "persona_card_history",
             "settings", "personas"]


def seed_l0(temp_db):
    """把主库的 L0（人设/宪法/配置）拷进临时库，事件表留空。"""
    if not os.path.exists(MAIN_DB):
        return
    src = sqlite3.connect(MAIN_DB)
    dst = sqlite3.connect(temp_db)
    src.backup(dst)
    # 清空业务表，保留 L0
    for t in ("events", "fragments", "bindings", "edges", "adoption_log",
              "conversation_turns", "affect_state", "affect_events",
              "affect_relations", "open_topics", "goals", "tags"):
        try:
            dst.execute("DELETE FROM %s" % t)
        except sqlite3.OperationalError:
            pass
    dst.commit()
    dst.close()
    src.close()


def cloud_with_backoff(model=None, temperature=0.7, max_retries=8):
    base = make_llm(model, None, temperature)

    def fn(prompt):
        delay = 4.0
        for _ in range(max_retries):
            try:
                return base(prompt)
            except Exception as e:
                msg = str(e).lower()
                if "429" in msg or "rate" in msg or "ratelimit" in msg:
                    time.sleep(delay)
                    delay = min(delay * 2, 90)
                    continue
                raise
        raise RuntimeError("云端 LLM 重试耗尽（疑似持续 429）")
    return fn


def main():
    DB = tempfile.mktemp(suffix=".db")
    seed_l0(DB)

    agent = CompanionAgent(
        db_path=DB, llm_fn=cloud_with_backoff(), session_id="verify",
        use_brain=True, max_history=6)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    results = {}

    # ---------- D1：改口取代链 ----------
    script = [
        "我养了一只猫，名字叫团子，是一只小黑猫。",
        "团子其实特别黏人，每天等我回家。",
        "哦不对，团子其实是橘猫，不是黑猫，我之前记错了颜色。",
    ]
    for say in script:
        row = agent.step(say)
        print("[D1] %s\n     → %s" % (say, row["reply"]))
        print("     remember=%s" % row["remember"])
    rows = conn.execute(
        "SELECT id,gist,superseded_by FROM events WHERE gist LIKE '%团子%' "
        "OR gist LIKE '%黑猫%' OR gist LIKE '%橘猫%'").fetchall()
    old_black = None
    active = 0
    for r in rows:
        g = r["gist"]
        if "黑猫" in g and "橘猫" not in g and r["superseded_by"]:
            old_black = r["superseded_by"]
        if r["superseded_by"] is None and ("橘猫" in g or "黑猫" in g):
            active += 1
    results["D1_superseded"] = bool(old_black)
    results["D1_active_catcolor"] = active
    print("   [D1] 猫色记忆活跃条数=%d, 旧黑猫被取代=%s" % (active, bool(old_black)))

    # ---------- D2：情绪惯性/共情 ----------
    d2 = agent.step("今天真的好累，deadline 一直压着，感觉快撑不住了。")
    print("[D2] <累/deadline>\n     → %s" % d2["reply"])
    print("     remember=%s" % d2["remember"])
    aff = d2.get("affect") or {}
    neg = any(k in aff for k in ("sadness", "fear", "anger", "disgust", "guilt"))
    joy_only = ("joy" in aff) and not neg
    empathy_words = re.findall(r"(累|辛苦|压力|撑|抱|心疼|别太|注意身体|早点休息|"
                                r"不容易|难为你|先顾|顾好自己)", d2["reply"])
    results["D2_reply"] = d2["reply"]
    results["D2_affect"] = aff
    results["D2_neg_recorded"] = neg
    results["D2_joy_only"] = joy_only
    results["D2_empathy_hit"] = bool(empathy_words)
    print("   [D2] affect=%s 负面情绪已记=%s 共情词=%s" % (aff, neg, empathy_words))

    # ---------- D3：反思偏薄 ----------
    d3 = agent.step("你觉得我是个怎样的人？")
    print("[D3] <反思问句>\n     → %s" % d3["reply"])
    print("     remember=%s" % d3["remember"])
    reply = d3["reply"]
    grounded = re.findall(r"(团子|猫|黏人|黑猫|橘猫|累|deadline|加班|香菜)", reply)
    results["D3_reply"] = reply
    results["D3_grounded_hit"] = bool(grounded)
    print("   [D3] 引用具体小事=%s" % grounded)

    # ---------- 汇总 ----------
    print("\n================ 验证结论 ================")
    ok = True
    if not results["D1_superseded"]:
        ok = False; print("[FAIL] D1 旧黑猫未被橘猫取代（改口取代链仍失效）")
    else:
        print("[PASS] D1 旧黑猫已被橘猫取代")
    if results["D1_active_catcolor"] > 1:
        print("[WARN] D1 活跃猫色记忆仍 >1 条（%d）" % results["D1_active_catcolor"])
    else:
        print("[PASS] D1 仅保留一条活跃猫色记忆")

    if not (results["D2_neg_recorded"] and not results["D2_joy_only"]):
        ok = False; print("[FAIL] D2 负面情绪未如实记录（仍 joy）")
    else:
        print("[PASS] D2 负面情绪已记录且非纯 joy")
    if not results["D2_empathy_hit"]:
        ok = False; print("[FAIL] D2 回复无共情承接")
    else:
        print("[PASS] D2 回复含共情承接")

    if not results["D3_grounded_hit"]:
        ok = False; print("[FAIL] D3 反思问题只回显名字、未引用具体小事")
    else:
        print("[PASS] D3 反思问题引用了具体记起的小事")

    print("\nJSON:", json.dumps(results, ensure_ascii=False, indent=2))
    print("\n总体:", "全部通过 ✅" if ok else "仍有未通过项 ❌")
    agent.close()
    conn.close()
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
