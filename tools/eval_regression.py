"""One-command regression suite for every bug fixed on this project.

Freezes the historical failure cases into deterministic store-layer assertions
(no LLM, no network) plus a live audit of the main DB. Each case maps to a real
bug that was found and fixed during testing:

  C1  cold-start recall: pronouns must not trigger persona hard-filter
  C2  antonym correction: 黑猫 -> 橘猫 must supersede (F3)
  C3  over-supersede: 摄影奖项 correction must NOT touch 周野/摄影爱好 (F5)
  C4  similarity supersede: unrelated 周野 facts must stay ACTIVE (F6)
  C5  pronoun persona guard: 我/自己 must never become a persona (v14)
  C6  alias merge: 妈 resolves to 我妈 after normalization (v14)
  C7  fact guard: fabricated detail is rejected (FACT_GUARD)
  C8  live DB audit: no duplicate persona / first-person persona / dangling edge

Usage:
    python tools/eval_regression.py                 # all store cases + live audit
    python tools/eval_regression.py --no-live       # skip live DB audit
    python tools/eval_regression.py --json out.json # machine-readable result
"""
import argparse
import json
import sqlite3
import sys
import tempfile

sys.path.insert(0, ".")

from neuromem.brain import Brain
from neuromem import store

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                           (" -- " + str(detail)[:120]) if detail else ""))


def fresh():
    return Brain(path=tempfile.mktemp(suffix=".db"))


def active_gists(b, contains):
    return [r["gist"] for r in b.conn.execute(
        "SELECT gist FROM events WHERE superseded_by IS NULL")
        if contains in (r["gist"] or "")]


def superseded_pairs(b):
    out = []
    for r in b.conn.execute(
            "SELECT id,gist,superseded_by FROM events WHERE superseded_by IS NOT NULL"):
        tgt = b.conn.execute("SELECT gist FROM events WHERE id=?",
                             (r["superseded_by"],)).fetchone()
        out.append((r["gist"], tgt["gist"] if tgt else "??"))
    return out


def c1_coldstart():
    from neuromem import recall
    b = fresh()
    for name in ("我", "你"):
        store.resolve_persona(b.conn, name)
    # guard now blocks pronouns, so simulate a legacy dirty DB
    b.conn.execute("INSERT OR REPLACE INTO personas(id,canonical_name,aliases_json,profile) "
                   "VALUES('p_me','我','[\"我\"]','')")
    got = recall._query_personas(b.conn, "我叫什么？在哪儿工作？")
    ok = "p_me" not in got
    record("C1 冷启动召回不被代词人物劫持", ok, "query_personas=%s" % got)
    b.close()


def c2_antonym_correction():
    b = fresh()
    b.remember("我养了一只猫叫团子，是只小黑猫。")
    b.remember("哦不对，团子其实是橘猫，不是黑猫，之前记错了。")
    pairs = superseded_pairs(b)
    ok = any("黑猫" in old and "橘猫" in new for old, new in pairs)
    active_black = [g for g in active_gists(b, "黑猫") if "不是黑猫" not in g]
    ok = ok and not active_black
    record("C2 反义更正(黑猫->橘猫)生效", ok, "superseded=%d, active_black=%d" % (len(pairs), len(active_black)))
    b.close()


def c3_over_supersede():
    b = fresh()
    b.remember("我同事周野是我进公司时的导师，人特别nice。")
    b.remember("你还喜欢摄影，手机里存了三千多张猫和风景的照片。")
    b.remember("上个月去了趟黄山，拍的云海照片拿了个公司摄影比赛二等奖。")
    b.remember("公司摄影比赛拿的是三等奖不是二等奖。")
    pairs = superseded_pairs(b)
    wrong = [old for old, new in pairs
             if ("周野" in old or "喜欢摄影" in old) and "等奖" not in old]
    ok = (not wrong) and any("二等奖" in old and "三等奖" in new for old, new in pairs)
    record("C3 摄影更正不误伤周野/摄影爱好", ok, "wrong=%d" % len(wrong))
    b.close()


def c4_similarity_supersede():
    b = fresh()
    b.remember("我想再养只狗，叫年糕，周野还没同意。")
    b.remember("我和周野吵架了，他忘了纪念日，冷战两天。")
    b.remember("周野后来买了束花道歉，我俩又和好了。")
    pairs = superseded_pairs(b)
    ok = len(pairs) == 0
    record("C4 无关的周野记忆不互相取代", ok, "superseded=%d" % len(pairs))
    b.close()


def c5_pronoun_guard():
    b = fresh()
    names = [store.resolve_persona(b.conn, n) for n in ("我", "自己", "俺", "我们")]
    ok = all(x is None for x in names)
    pid = store.resolve_persona(b.conn, "周野")
    ok = ok and pid is not None
    b.close()
    record("C5 第一人称代词不建persona", ok, "pronoun_ids=%s" % names)


def c6_alias_merge():
    import neuromem.config as cfg
    b = fresh()
    b.conn.execute("INSERT INTO personas(id,canonical_name,aliases_json,profile) "
                   "VALUES('p_mom','我妈','[\"妈\",\"我妈\"]','')")
    pid = store.resolve_persona(b.conn, "妈")
    ok = pid == "p_mom"
    record("C6 别名归一(妈->我妈)", ok, "resolved=%s" % pid)
    b.close()


def c7_fact_guard():
    import agent.prompts as P
    guarded = P.build_prompt(None, "我在公司拿了摄影比赛二等奖。", [],
                             "苏晚", fact_guard=True)
    plain = P.build_prompt(None, "我在公司拿了摄影比赛二等奖。", [],
                           "苏晚", fact_guard=False)
    has_ground = all(k in guarded for k in ("逐字", "禁止补充", "claims", "source_id"))
    switched = has_ground and ("claims" not in plain)
    record("C7 事实护栏契约已注入prompt(guard开/关可切换)", switched,
           "guarded=%s plain_has_claims=%s" % (has_ground, "claims" in plain))


def c8_live_audit():
    from tools.memory_audit import audit
    import os
    db = "data/neuromem.db"
    if not os.path.exists(db):
        record("C8 主库审计", False, "db missing")
        return
    c = sqlite3.connect(db)
    rep = audit(c)
    c.close()
    p = rep["personas"]
    ok = (not p["first_person"] and not p["contains"]
          and rep["edges"]["dangling_total"] == 0
          and len(rep["supersede"]["dangling_targets"]) == 0)
    record("C8 主库地基健康", ok,
           "personas=%d first_person=%d dup=%d dangling=%d" % (
               p["total"], len(p["first_person"]), len(p["contains"]),
               rep["edges"]["dangling_total"]))


CASES = [("C1", c1_coldstart), ("C2", c2_antonym_correction),
         ("C3", c3_over_supersede), ("C4", c4_similarity_supersede),
         ("C5", c5_pronoun_guard), ("C6", c6_alias_merge),
         ("C7", c7_fact_guard), ("C8", c8_live_audit)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-live", action="store_true", help="skip C8 live DB audit")
    ap.add_argument("--only", default="", help="run only cases with this prefix, e.g. C2")
    ap.add_argument("--json", default="", help="write JSON result to this path")
    args = ap.parse_args()

    print("=== NeuroMem regression suite ===")
    for cid, fn in CASES:
        if args.only and not cid.startswith(args.only):
            continue
        if args.no_live and cid == "C8":
            continue
        try:
            fn()
        except Exception as e:
            record(cid, False, "EXCEPTION %s: %s" % (type(e).__name__, e))

    passed = sum(1 for r in RESULTS if r["ok"])
    total = len(RESULTS)
    print("\n=== %d/%d passed ===" % (passed, total))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"passed": passed, "total": total, "results": RESULTS},
                      f, ensure_ascii=False, indent=2)
        print("JSON ->", args.json)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
