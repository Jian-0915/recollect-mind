"""agent 链路测试：多轮对话，验证记忆的写入/检索/回答/代谢。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem.memory import Memory

DB = Path(__file__).resolve().parent.parent / "data" / "agent.db"


def main():
    if DB.exists():
        DB.unlink()
    m = Memory(str(DB), user_name="小明")

    turns = [
        "我叫小明，我特别怕狗。",
        "我家住在公园附近。",
        "昨天在公园又被狗吓到了，很害怕。",
        "小红是我同事，她今天拿到offer了，特别开心。",
        "小明害怕什么？",
        "小红最近怎么样？",
    ]
    for u in turns:
        ans, items = m.observe(u)
        print("用户>", u)
        print("Agent>", ans)
        print("   召回 %d 条: %s" % (len(items), "；".join(it["gist"] for it in items[:4])))
        print()

    print("=== 代谢 evolve ===")
    r = m.evolve()
    print("新概念:", [c["text"] for c in r["new_concepts"]])
    print("升层:", [c["text"] for c in r["abstracted"]])
    print("快照:", r["snapshot"])
    print("统计:", m.stats())
    m.close()


if __name__ == "__main__":
    main()