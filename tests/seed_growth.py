"""补充成长数据：40+ 条真实感的经历，覆盖日常/情绪/人物/意外/反思。

用法：
    python tests/seed_growth.py            # 只写入
    python tests/seed_growth.py --evolve   # 写完并跑一轮代谢

每条都会调用抽取模型 + 嵌入模型，约 3~6 秒/条。
"""
import argparse
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import db, evolve, store  # noqa: E402

MEMORIES = [
    # 日常与疲惫
    "今天在公司加班到十点，眼睛都花了。",
    "小周陪我一起加班，还给我买了咖啡。",
    "连续三天加班，回家倒头就睡。",
    "加班太多，我开始怀疑这份工作值不值。",
    "今天又加班，回家路上看到月亮很圆。",
    # 小林：大学同学
    "小林是我的大学同学，关系一直很好。",
    "小林周末约我去爬山，我们聊了很多。",
    "小林失恋了，我陪他喝了一晚上酒。",
    "小林说他要搬到另一个城市工作。",
    "临走前小林把一盆绿萝送给了我。",
    # 阿哲：最好的朋友
    "阿哲是我最好的朋友，我们从小学就认识。",
    "阿哲今天请我吃饭，说他升职了。",
    "阿哲借钱给亲戚，结果被拖了半年。",
    "阿哲劝我别总熬夜，语气像个老头子。",
    # 家人
    "妈妈打电话说家里的辣椒结果了。",
    "妈妈总担心我吃不好，每次都问三遍。",
    "周末回家，妈妈做了一桌菜。",
    "妈妈的腰又疼了，我陪她去医院。",
    "爸爸在院子里修自行车，一句话不说。",
    "奶奶生日，全家人都回去了。",
    # 焦虑与自我反思
    "最近总是睡不好，半夜会醒。",
    "我在想自己到底想做什么。",
    "看到同龄人都在升职，我有点慌。",
    "今天把手机放一边，读了一下午书，心静了。",
    "写周报的时候发现自己这周什么都没做成。",
    "决定每周留一天不加班。",
    # 意外与突发（高惊讶）
    "楼下便利店突然关门了，老板说要回老家。",
    "骑电动车摔了一跤，膝盖擦破了。",
    "收到一笔两年前的退款，很意外。",
    "隔壁搬来一个弹钢琴的邻居。",
    "在公司电梯里遇到十年没见的初中同桌。",
    # 小成就
    "今天把拖了三个月的小项目做完了。",
    "第一次自己做了一顿饭，味道还行。",
    "跑步坚持到五公里了。",
    "学会用新的工具，效率高了不少。",
    # 邻里
    "王姨送了一篮自己种的番茄。",
    "王姨说她孙子考上大学了。",
    "帮王姨搬了一袋米上楼。",
    # 自然与感官
    "公园的桂花开了，香气很浓。",
    "昨晚下了很大的雨，窗户啪啪响。",
    "早上看到一只橘猫在车顶上晒太阳。",
    "天气突然降温，忘记带外套。",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evolve", action="store_true", help="写完后跑一轮代谢")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    c = db.connect()
    items = MEMORIES[:args.limit] if args.limit else MEMORIES
    before = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    ok, failed, surprises = 0, 0, []
    t0 = time.time()

    for i, text in enumerate(items, 1):
        try:
            ids = store.remember(c, text, speaker="用户")
            ok += 1
            rows = c.execute(
                "SELECT surprise FROM events WHERE id=?", (ids[0],)).fetchone()
            s = rows["surprise"] if rows and rows["surprise"] is not None else -1
            surprises.append(s)
            print("[%02d/%d] %.2f  %s" % (i, len(items), s, text[:26]))
        except Exception as e:
            failed += 1
            print("[%02d/%d] FAIL %s | %s" % (i, len(items), text[:20], str(e)[:120]))

    after = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print("\n写入完成：成功 %d · 失败 %d · 新事件 %d · 用时 %.1fs"
          % (ok, failed, after - before, time.time() - t0))
    if surprises:
        avg = sum(max(s, 0) for s in surprises) / len(surprises)
        high = sum(1 for s in surprises if s >= 0.7)
        print("惊讶度：均值 %.2f · 高意外(≥0.7) %d 条" % (avg, high))

    if args.evolve:
        print("\n跑一轮代谢…")
        r = evolve.tick(c)
        print("新概念 %d · 再抽象 %d · 归档 %d · 自传 %s"
              % (len(r.get("new_concepts") or []), len(r.get("abstracted") or []),
                 (r.get("decay") or {}).get("newly_archived", 0),
                 "有" if r.get("narrative") else "无"))
        for nc in (r.get("new_concepts") or []):
            print("  + [L%d] %s（%d 条经历）" % (nc["level"], nc["text"][:40], nc["n"]))
    c.close()


if __name__ == "__main__":
    main()