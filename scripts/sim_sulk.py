"""冷战期回话验证：气没消的时候，它的话到底会不会变短、变冷、变软。

提示词约束不住模型（要求「只回五个字」，实测照样写一整句），所以这里不是
看提示词有没有生效，是看真实生成 + 出口裁剪之后，用户读到的是什么。

用法：
  python scripts/sim_sulk.py          不调模型，只打印温度曲线
  python scripts/sim_sulk.py --llm    真调模型，逐句看话是怎么变软的
"""
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DST = ROOT / "data" / "_sulk_sim.db"
USE_LLM = "--llm" in sys.argv
if DST.exists():
    DST.unlink()

# 必须在 import neuromem 之前设好，否则 config 已经锁定主库路径
os.environ["NEUROMEM_DB"] = str(DST)

from neuromem import companion, db, motivation, persona_card  # noqa: E402

USER = "阿剑"
AGENT = "小满"


def build_db():
    c = db.connect(str(DST))
    card = persona_card.template(example=True)
    card["name"] = AGENT
    card["call_user"] = USER
    card["role"] = "陪着对方的人，说话直、不太会装"
    card["relationship"]["initiative"] = "偶尔主动，有依据才提"
    persona_card.apply(c, card, actor="human", reason="冷战略模拟")
    for i in range(20):
        companion.remember_turn(c, USER, ["今天加班", "吃了碗面", "林晚又来叫我"][i % 3])
    return c


def main():
    c = build_db()
    # 冷战从 20 分钟前开始，总长 10 小时 —— 对应「刚开始最冷」那一段
    started = datetime.now().astimezone() - timedelta(minutes=20)
    motivation.sulk_start(c, "林晚", now=started, hours=10)
    print("冷战开始：%s（10 小时）\n" % started.strftime("%H:%M"))
    print("解释：你因为对方满嘴林晚生了闷气，现在对方回来搭话了。\n")

    turns = [
        "在吗",
        "昨天是我不对，一直说林晚的事，没顾上你",
        "真的，你别生气了，我以后注意",
        "晚上我陪你，想吃什么我去买",
        "那个……你还理我吗",
    ]
    agent = None
    if USE_LLM:
        from agent.companion import CompanionAgent
        agent = CompanionAgent(db_path=str(DST), session_id="sulk_sim",
                               trace_path=None, max_history=10)

    for say in turns:
        state = motivation.chat_state(c, say) if not agent else None
        if agent:
            row = agent.step(say)
            state = {
                "active": bool((row.get("sulk") or {}).get("active")),
                "temper": (row.get("sulk") or {}).get("temper"),
                "turns": (row.get("sulk") or {}).get("turns"),
            }
            reply = row.get("reply") or ""
        else:
            reply = "（未调模型）"
        t = state.get("temper")
        bar = "" if t is None else " " + "#" * int(round(float(t) * 10))
        print("脸  色：%s%s" % ("已消气" if not state.get("active")
                              else "冷 %.2f" % float(t or 0), bar))
        print("对  方：%s" % say)
        print("小  满：%s" % reply)
        print("-" * 46)

    if agent:
        agent.close()
    c.close()


if __name__ == "__main__":
    main()
