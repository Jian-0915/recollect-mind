"""持续生成成长数据：人物专属事件 + 时间线，可反复运行。

用法：
    python tests/simulate_growth.py --count 30 --days 14 --evolve

与第一版相比：事件按人物绑定，不再随机交叉（避免"奶奶在咖啡馆和客户吵架"这种荒诞组合）。
建议先 `python service.py stop` 再跑，避免与自动进化抢写锁。
"""
import argparse
import os
import random
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import db, evolve, store  # noqa: E402

PROFILES = {
    "阿哲": [
        ("在楼下的小馆子请我吃饭，说他升职了", "挺开心"),
        ("约我去水库钓鱼，晒了一下午", "很放松"),
        ("劝我别总熬夜，语气像个老头子", "有点想笑"),
        ("借钱给亲戚，被拖了半年才还", "替他憋屈"),
    ],
    "小林": [
        ("周末约我去爬山，一路聊了很多", "很尽兴"),
        ("失恋那阵子，我陪他喝了一晚上酒", "有点心酸"),
        ("说下个月要搬去另一个城市工作", "舍不得"),
        ("临走前把一盆绿萝留给了我", "心里空了一块"),
    ],
    "小周": [
        ("陪我一起加班，还给我买了咖啡", "挺感动"),
        ("在准备考一个证，每晚学到很晚", "佩服他"),
        ("第一次独立带项目，紧张得睡不着", "替他捏把汗"),
    ],
    "妈妈": [
        ("打电话说家里的橘子熟了，让我回去拿", "想家了"),
        ("总担心我吃不好，每次都要问三遍", "有点好笑"),
        ("腰又疼了，我陪她去医院拍了片", "心里发紧"),
        ("学会用手机视频了，第一次打过来笑个不停", "很暖"),
    ],
    "爸爸": [
        ("在院子里修自行车，一句话不说", "习惯了"),
        ("把葡萄架重新搭了一遍，手上全是划痕", "有点心疼"),
        ("难得主动问我工作累不累", "愣住了"),
    ],
    "王姨": [
        ("送了一篮自己种的番茄", "不好意思"),
        ("说她孙子考上大学了，笑得合不拢嘴", "替她高兴"),
        ("让我帮忙把一袋米搬上楼", "顺手的事"),
    ],
    "奶奶": [
        ("生日那天，全家人都回去了", "很热闹"),
        ("记不清我的名字，但记得我爱吃红烧肉", "鼻子有点酸"),
        ("在阳台上晒了一下午太阳，说这样最舒服", "安静"),
    ],
    "表哥": [
        ("发消息说要结婚了，让我当伴郎", "又惊又喜"),
        ("换了新工作，工资涨了不少", "替他高兴"),
    ],
}

SOLO = [
    ("加班到十点，眼睛都花了", "公司", "有点累"),
    ("把拖了三个月的项目收尾", "公司", "松了口气"),
    ("第一次自己做了一顿饭，味道居然还行", "家里", "挺得意"),
    ("跑步坚持到五公里", "小区外环", "挺有成就感"),
    ("读了一下午书，手机放在一边", "图书馆", "心静了"),
    ("骑电动车摔了一跤，膝盖擦破", "路口", "又疼又气"),
    ("收到一笔两年前的退款", "家里", "意外"),
    ("在电梯里遇到十年没见的初中同桌", "公司", "愣了半天"),
    ("夜里又醒了，翻来覆去睡不着", "家里", "有点慌"),
    ("看到同龄人升职的消息，心里发紧", "家里", "不是滋味"),
    ("决定每周留一天不加班", "家里", "踏实"),
]


def time_phrase(days_ago, with_time=True):
    if not with_time:
        return ""
    if days_ago == 0:
        return "今天"
    if days_ago == 1:
        return "昨天"
    if days_ago == 2:
        return "前天"
    if days_ago <= 6:
        return "%d 天前" % days_ago
    if days_ago <= 13:
        return "上周"
    if days_ago <= 20:
        return "上上周"
    if days_ago <= 27:
        return "前一阵"
    return "上个月"


def make_line(rng, days_ago):
    prefix = time_phrase(days_ago, with_time=rng.random() < 0.75)
    if rng.random() < 0.65:
        who = rng.choice(list(PROFILES))
        act, feel = rng.choice(PROFILES[who])
        return "%s%s%s，%s。" % (prefix, who, act, feel)
    act, place, feel = rng.choice(SOLO)
    return "%s我在%s%s，%s。" % (prefix, place, act, feel)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--evolve", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()

    rng = random.Random(a.seed)
    c = db.connect()
    before = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    plan = [rng.randint(0, a.days) for _ in range(a.count)]
    rng.shuffle(plan)

    added, failed, surprises = 0, 0, []
    seen = set()
    t0 = time.time()
    for i, days_ago in enumerate(plan, 1):
        text = make_line(rng, days_ago)
        for _ in range(3):
            if text in seen:
                text = make_line(rng, days_ago)
            else:
                break
        seen.add(text)

        ok = False
        for attempt in range(3):
            try:
                ids = store.remember(c, text, speaker="用户")
                ok = True
                break
            except Exception as e:
                if "locked" in str(e).lower():
                    time.sleep(0.8)
                    continue
                print("  [%02d] FAIL %s | %s" % (i, text[:22], str(e)[:100]))
                break
        if not ok:
            failed += 1
            continue
        added += 1
        if ids:
            row = c.execute("SELECT surprise FROM events WHERE id=?", (ids[0],)).fetchone()
            if row and row["surprise"] is not None:
                surprises.append(row["surprise"])
        print("[%02d/%d] %s" % (i, len(plan), text[:44]))

    after = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print("\n写入：成功 %d · 失败 %d · 新事件 %d · 用时 %.1fs"
          % (added, failed, after - before, time.time() - t0))
    if surprises:
        print("惊讶：均值 %.2f · 最高 %.2f"
              % (sum(surprises) / len(surprises), max(surprises)))

    if a.evolve:
        print("\n跑一轮代谢…")
        r = evolve.tick(c)
        print("新概念 %d · 再抽象 %d · 归档 %d"
              % (len(r.get("new_concepts") or []), len(r.get("abstracted") or []),
                 (r.get("decay") or {}).get("newly_archived", 0)))
        for nc in (r.get("new_concepts") or []):
            print("  + [L%d] %s" % (nc["level"], nc["text"][:40]))
    c.close()


if __name__ == "__main__":
    main()