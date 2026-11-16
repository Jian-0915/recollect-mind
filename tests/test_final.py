"""测试情感状态机 + 自我叙事 + 表征漂移。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem import db, drift, emotion, persona, selfnarrative
from neuromem.store import remember

DB = Path(__file__).resolve().parent.parent / "data" / "test_final.db"


def cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def main():
    if DB.exists():
        DB.unlink()
    conn = db.connect(str(DB))
    persona.ensure(conn, "用户")
    for s in [
        "用户叫小明。",
        "用户喜欢草莓。",
        "用户怕狗。",
        "用户昨天在公园被狗吓到，很害怕。",
    ]:
        remember(conn, s, speaker="用户")

    print("=== 情感状态机 ===")
    es = emotion.EmotionState(conn)
    print("初始:", es.get())
    print("开心事件:", es.update(0.9, 0.7))
    print("恐惧事件:", es.update(-0.9, 0.9))
    print("衰减一次:", es.decay())

    print("\n=== 自我叙事 ===")
    print(selfnarrative.build(conn, "用户"))

    print("\n=== 表征漂移 ===")
    r = conn.execute("SELECT id,embedding FROM events LIMIT 1").fetchone()
    before = db.unpack(r["embedding"])
    n = drift.drift(conn)
    print("漂移事件数:", n)
    after = db.unpack(conn.execute("SELECT embedding FROM events WHERE id=?", (r["id"],)).fetchone()["embedding"])
    print("漂移前后自相似:", round(cos(before, after), 5))
    conn.close()


if __name__ == "__main__":
    main()