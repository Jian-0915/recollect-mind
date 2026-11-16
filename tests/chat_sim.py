"""模拟 chat 链路：验证回答不再退化、寒暄不入库。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem.memory import Memory

DB = Path(__file__).resolve().parent.parent / "data" / "chat_sim.db"


def main():
    if DB.exists():
        DB.unlink()
    m = Memory(str(DB), user_name="用户")

    turns = [
        "你好",
        "我喜欢吃草莓",
        "我喜欢吃什么？",
        "小明喜欢吃什么？",
    ]
    for u in turns:
        ans, items = m.observe(u)
        print("你>", u)
        print("Agent>", ans)
        print("   (召回 %d 条)" % len(items))
        print()

    print("统计:", m.stats())
    m.close()


if __name__ == "__main__":
    main()