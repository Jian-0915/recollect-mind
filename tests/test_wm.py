"""测试工作记忆 / 目标：目标相关性影响进入工作台的顺序 + 容量限制。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem.working_memory import GoalManager, WorkingMemory

ITEMS = [
    {"gist": "小明喜欢草莓", "kind": "verified", "score": 0.90, "fragments": []},
    {"gist": "小明在公司处理工作", "kind": "verified", "score": 0.60, "fragments": []},
    {"gist": "小明周末去爬山", "kind": "verified", "score": 0.50, "fragments": []},
]


def show(title, slots):
    print(title)
    for it in slots:
        print("   ", it["gist"])
    print()


def main():
    wm = WorkingMemory(capacity=2)
    show("=== 无目标（容量2）===", wm.fill(ITEMS))
    show("=== 目标=工作 ===", wm.fill(ITEMS, goal="工作"))
    show("=== 目标=草莓 ===", wm.fill(ITEMS, goal="草莓"))

    print("工作台 context:")
    print(wm.context())

    gm = GoalManager()
    gm.push("帮用户安排周末")
    gm.push("先查天气")
    print("\n目标栈:", gm.stack(), "| 当前目标:", gm.current())
    gm.pop()
    print("弹出一个后, 当前目标:", gm.current())


if __name__ == "__main__":
    main()