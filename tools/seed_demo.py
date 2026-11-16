"""灌一组**虚构**记忆到独立 demo 库，方便第一次打开界面时不看到空库。

刻意不写主库（`data/neuromem.db`），也不调用 LLM：事件文本是预写好的，
只有向量需要嵌入模型。没有 ollama 时脚本仍会灌文本并明确提示"召回会退化"，
这样别人 clone 下来也能先看界面和数据结构。

用法：
    python tools/seed_demo.py                # 默认 data/demo.db
    python tools/seed_demo.py --db my.db --force
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 先把库指到 demo，再导入 neuromem —— 否则 config.DB_PATH 会落在主库。
DEMO_DEFAULT = ROOT / "data" / "demo.db"

# (gist, subject, 几天前, 显著性)
MEMORIES = [
    ("你养了只橘猫叫团子，特别黏人", "counterpart", 120, 0.7),
    ("你最爱吃菌菇火锅，不喜欢香菜", "counterpart", 90, 0.6),
    ("你在杭州做设计，最近在赶一个甲方项目", "counterpart", 30, 0.6),
    ("你计划明年夏天去西藏自驾，已经在看租车", "counterpart", 14, 0.8),
    ("你每周三晚上固定去上瑜伽课，算是唯一的放松", "counterpart", 40, 0.6),
    ("你妈一直想去日本看红叶，说京都秋天最好", "counterpart", 60, 0.6),
    ("你最近在学网页对话那套东西，卡在上下文管理上", "counterpart", 7, 0.6),
    ("你大学室友叫周野，现在在深圳做游戏", "counterpart", 200, 0.5),
    ("阿岚养了只狗叫布丁", "third_party", 45, 0.6),
    ("阿岚上周生日，你给她烤了戚风蛋糕，她说比店里好吃", "third_party", 20, 0.7),
    ("你们上次聊到小时候在外婆家长大，夏天睡竹床", "shared", 10, 0.7),
    ("我答应过你：聊到工作压力时不催你，先听你说完", "self", 5, 0.7),
]

PERSONAS = [
    ("阿岚", "person", "social"),
    ("团子", "pet", "mentioned"),
    ("周野", "person", "social"),
    ("妈妈", "person", "social"),
]


def _id(prefix):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:12])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEMO_DEFAULT))
    ap.add_argument("--force", action="store_true", help="库里已有事件时仍然追加")
    a = ap.parse_args()

    import os
    os.environ["NEUROMEM_DB"] = a.db

    from neuromem import config, db
    config.DB_PATH = Path(a.db)

    conn = db.connect(Path(a.db))
    n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    if n and not a.force:
        print("这个库已有 %d 条事件：%s" % (n, a.db))
        print("要重灌请加 --force，或换个 --db 路径。")
        return 1

    gists = [m[0] for m in MEMORIES]
    vectors, warn = [], ""
    try:
        from neuromem import embed
        vectors = embed.embed_many(gists)
        if len(vectors) != len(gists):
            vectors = []
            warn = "嵌入数量不匹配，已跳过向量"
    except Exception as e:
        warn = "嵌入不可用（%s），只灌文本；召回会退化成关键词" % type(e).__name__

    now = datetime.now()
    with conn:
        for i, (gist, subject, days_ago, sal) in enumerate(MEMORIES):
            ts = now - timedelta(days=days_ago)
            ev_id = _id("evt")
            vec = db.pack(vectors[i]) if vectors else None
            conn.execute(
                "INSERT INTO events (id, created_at, occurred_at, gist, subject,"
                " salience, confidence, provenance, activation, archived, embedding)"
                " VALUES (?,?,?,?,?,?,?,?,?,0,?)",
                (ev_id, ts.isoformat(timespec="seconds"), ts.isoformat(timespec="seconds"),
                 gist, subject, sal, 0.8, "demo_seed", 1.0, vec))
            conn.execute(
                "INSERT INTO fragments (id, text, modality, embedding, salience)"
                " VALUES (?,?,?,?,?)",
                (_id("frag"), gist, "text", vec, sal))
        for name, kind, bond in PERSONAS:
            conn.execute(
                "INSERT INTO personas (id, canonical_name, aliases_json, kind, bond)"
                " VALUES (?,?,?,?,?)",
                (_id("per"), name, json.dumps([], ensure_ascii=False), kind, bond))

    print("已灌入 %d 条虚构记忆 · %d 个人物/宠物 → %s"
          % (len(MEMORIES), len(PERSONAS), a.db))
    if warn:
        print("注意：" + warn)
    print("\n下一步：")
    print('  NEUROMEM_DB="%s" python -m neuromem serve 8000' % a.db)
    print("  打开 http://127.0.0.1:8000 ，或 POST /api/ask 只读检索。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
