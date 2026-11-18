"""模拟四种冲动在一周里怎么登场（只在副本库上跑，绝不碰主库）。

剧情：
  D-14 起 —— 两周的普通日常，作为「平时」的基线
  D0 傍晚 —— 有件事没聊完（麻辣烫），然后人不见了
  D1      —— 惦记：顺着那件事问一句
  D2      —— 事问完了还是没人，转成「人呢」
  D3      —— 回来了，但满嘴林晚 → 吃醋 → 冷战
  D4      —— 冷战到点，别扭地回来

用法：
  python scripts/sim_impulse.py          合成干净库（受控剧情，看机制对不对）
  python scripts/sim_impulse.py --real   主库副本（看真实数据会不会误触发）
  python scripts/sim_impulse.py --llm    真的调模型（慢，每条一次调用，看语气）
"""
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "data" / "neuromem.db"
DST = ROOT / "data" / "_impulse_sim.db"
USE_REAL = "--real" in sys.argv
USE_LLM = "--llm" in sys.argv

if DST.exists():
    DST.unlink()
if USE_REAL:
    if not SRC.exists():
        sys.exit("主库不存在：%s" % SRC)
    shutil.copy(SRC, DST)
# 必须在 import neuromem 之前设好，否则 config 已经加载主库路径了
os.environ["NEUROMEM_DB"] = str(DST)
os.environ.setdefault("NEUROMEM_PROACTIVE_QUIET_FROM", "23")
os.environ.setdefault("NEUROMEM_PROACTIVE_QUIET_TO", "8")

from neuromem import companion, db, motivation, persona_card, proactive  # noqa: E402

START = datetime(2026, 11, 17, 20, 0, 0, tzinfo=timezone(timedelta(hours=8)))
USER = "阿剑"


def say(c, text, at, speaker=USER):
    companion.remember_turn(c, speaker, text)
    c.execute("UPDATE conversation_turns SET ts=?, created_at=? WHERE id=("
              "SELECT id FROM conversation_turns ORDER BY rowid DESC LIMIT 1)",
              (at.isoformat(), at.isoformat()))
    c.execute("UPDATE companion_state SET last_turn_at=? WHERE id=1", (at.isoformat(),))
    c.commit()


def backbone(c):
    """两周的日常基线：话不多不少，偶尔提到林晚。"""
    for i in range(24):
        at = START - timedelta(days=14) + timedelta(hours=i * 13)
        say(c, ["今天加班", "吃了碗面", "好困", "林晚说她也要下班了"][i % 4], at)


def main():
    c = db.connect(str(DST))
    proactive._ensure(c)
    motivation._ensure(c)
    for t in ("proactive_deliveries", "proactive_suggestions", "sulk_state"):
        c.execute("DELETE FROM %s" % t)
    c.execute("DELETE FROM topic_state")
    c.commit()

    if not USE_REAL:
        card = persona_card.template(example=True)
        card["name"] = "小满"
        card["role"] = "陪着对方的人"
        card["relationship"]["initiative"] = "偶尔主动，有依据才提"
        card["relationship"]["call_user"] = USER
        persona_card.apply(c, card, actor="human", reason="模拟")
        backbone(c)

    c.execute("CREATE TABLE IF NOT EXISTS personas(id TEXT PRIMARY KEY, canonical_name TEXT, "
              "aliases_json TEXT, profile TEXT, profile_at TEXT, profile_events INTEGER, "
              "kind TEXT, bond TEXT)")
    c.execute("INSERT OR REPLACE INTO personas VALUES"
              "('p_lin','林晚','[\"林晚\"]','',NULL,0,'person','social')")
    c.execute("INSERT OR REPLACE INTO personas VALUES"
              "('p_mom','妈妈','[\"我妈\",\"妈妈\"]','',NULL,0,'person','social')")
    c.execute("INSERT OR REPLACE INTO personas VALUES"
              "('p_cat','麻薯','[\"麻薯\"]','',NULL,0,'pet','social')")
    c.execute("CREATE TABLE IF NOT EXISTS relations(id TEXT PRIMARY KEY, a TEXT, b TEXT, "
              "rel_type TEXT, strength REAL, since TEXT, until TEXT, evidence TEXT)")
    c.execute("INSERT OR REPLACE INTO relations VALUES"
              "('r_mom','p_mom','p_you','妈妈',0.5,'','','')")
    c.commit()

    if USE_REAL:
        c.execute("DELETE FROM companion_state")
    companion.update_state(c, open_topics=["下午的麻辣烫还去不去", "麻辣烫具体几点去吃"])
    say(c, "今天想吃麻辣烫，下午去不去？", START - timedelta(hours=1))

    # 剧情按时间推进：不能提前写进库，否则 tick 会看到「未来」的对话时间
    events = [
        # D3 回来聊，但满嘴林晚（顺带提了妈和猫，这两个不该醋）
        (START + timedelta(hours=50), "回来了，这两天忙晕了"),
        (START + timedelta(hours=50, minutes=8), "林晚今天又拉我去看花市"),
        (START + timedelta(hours=50, minutes=16), "林晚说那家花市最近有活动"),
        (START + timedelta(hours=50, minutes=24), "我妈给我寄了笋干，麻薯差点给拆了"),
        (START + timedelta(hours=51), "林晚还叫我周末陪她逛街，累"),
        (START + timedelta(hours=51, minutes=10), "林晚挑衣服是真慢"),
    ]
    ei = 0

    def llm_fn(prompt, **kw):
        from neuromem import llm
        return llm.generate(prompt, **kw)

    rows = []
    t = START
    end = START + timedelta(days=5)
    while t <= end:
        while ei < len(events) and START + (events[ei][0] - START) <= t:
            at, txt = events[ei]
            say(c, txt, at)
            ei += 1
        r = proactive.tick(c, t)
        p = r.get("proactive", {})
        if p.get("created"):
            got = proactive.peek(c, llm_fn=llm_fn if USE_LLM else (lambda p, **k: "（略）"),
                                 now=t)
            rows.append((t, got.get("kind"), got.get("text"), got.get("sulk")))
        t += timedelta(minutes=20)

    print("=" * 78)
    print("库：%s（%s；主库未改动）" % (DST.name, "主库副本" if USE_REAL else "合成干净库"))
    print("=" * 78)
    for ts, kind, text, sulk in rows:
        print("%s  %-8s  %s" % (ts.strftime("%m-%d %H:%M"), kind, text))
        if sulk:
            print("%22s  └─ 开始冷战，%.0f 小时到点（%s）" % (
                "", sulk["hours"], sulk["until"][11:16]))
    print("-" * 78)
    counts = {}
    for _, k, _, _ in rows:
        counts[k] = counts.get(k, 0) + 1
    print("五天开口 %d 次：%s" % (len(rows), counts))
    c.close()


if __name__ == "__main__":
    main()
