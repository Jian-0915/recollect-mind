"""持续聊天灌数据：以「用户」身份直连 CompanionAgent 在主库上做多轮真实对话。

用途：主库被清到初始态后，用一份连贯的人物剧本跑大量多轮对话，
积累丰富的记忆数据（事件 / 碎片 / 人物 / 概念 / 情绪 / 采纳），
供后续观察长效机制与 web 看板。

用法：
    python tools/rich_chat.py            # 默认跑全部剧本
    python tools/rich_chat.py --tail 20  # 只跑最后 20 句（续聊）
    python tools/rich_chat.py --db data/neuromem.db

注意：
- 直接写主库 data/neuromem.db（与 web 同一库），web 可实时观察。
- 带 429 指数退避，云端限流不会让整轮中断。
- 剧本是一份连贯的用户人生设定（苏晚 / 团子橘猫 / 云栖科技 / 阿岚周野等），
  包含 2 处更正（周野是男友、摄影比赛三等奖）用于验证取代链。
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from neuromem import llm
from agent.companion import CompanionAgent


# ------------------------------------------------------------------ 剧本
USER_LINES = [
    # 开场 / 自我介绍 / 宠物
    "嗨，我是苏晚。今天第一次跟你聊天，有点好奇你会不会记得我。",
    "我养了一只猫，叫团子，是一只橘猫，胖乎乎的特别黏人。",
    "团子是我去年在楼下捡的流浪猫，当时它瘦得只剩一把骨头，现在被我养得圆滚滚。",

    # 工作
    "我在一家叫云栖科技的SaaS公司做后端工程师，主要写Go。",
    "这周特别崩溃，我们的支付模块要在周五上线，我连续加班三天了。",
    "昨晚改bug到两点，今早八点又得爬起来开会，感觉自己快散架了。",
    "其实我挺喜欢写代码的，就是这种临上线前的压迫感太折磨人。",

    # 朋友（此处周野只说是同事/导师，后面再更正为男友）
    "我有个闺蜜叫阿岚，我们大学就认识了，现在还住同一个城市。",
    "阿岚上周生日，我给她烤了个戚风蛋糕，她说比蛋糕店卖的还好吃。",
    "我同事周野是我进公司时的导师，人特别nice，教会了我好多。",

    # 偏好
    "我超爱手冲咖啡，每天早上不喝点就打不起精神，最近迷上了埃塞俄比亚的豆子。",
    "但我特别讨厌香菜，闻到那个味道就受不了，每次吃火锅都得单独跟服务员说不要香菜。",
    "我喜欢下雨天，听着雨声写代码特别安心。",
    "对了，我有点恐高，上次去爬山差点吓哭，但还是坚持登顶了。",

    # 家人
    "我妈在杭州，我爸退休后在家养花，他们感情特别好。",
    "外婆最近身体不太好，在老家住院，我有点想请假回去看看她。",
    "我其实挺想家的，一个人在这个城市租房住，有时候挺孤单。",

    # 爱好
    "我周末喜欢烘焙，除了戚风还会做曲奇和吐司，朋友圈都叫我烘焙小能手。",
    "我还喜欢摄影，手机里存了三千多张猫和风景的照片。",
    "上个月去了趟黄山，拍的云海照片拿了个公司摄影比赛二等奖。",
    "爬山虽然恐高，但山顶的风景真的治愈我。",

    # 情绪低点（验证共情/情绪记录）
    "今天又加班到很晚，回来团子在门口等我，突然就有点想哭。",
    "你说，人是不是越长大越难开心？我感觉自己最近总是很累。",

    # 更正①：周野其实是男友（之前只说是同事/导师）
    "啊对了，前两天我说周野是我同事，其实说错了——他是我男友，我们在一起快半年了，之前是同事来着。",

    # 旅行计划（兴奋）
    "我打算春天请年假去大理玩，已经看好了洱海边的民宿。",
    "想带团子一起去，但怕它坐飞机应激，可能还是放阿岚家寄养。",
    "大理的苍山洱海我向往好久了，想拍一组旅行大片。",
    "要是能和周野一起环洱海骑行就好了，他骑车超厉害的。",

    # 更多日常（丰富数据）
    "今天中午和阿岚吃了家云南菜，尝了鲜花饼，意外地好吃。",
    "我们组新来了个实习生叫小赵，是个00后，特别有活力。",
    "周野今天给我带了早餐，是我最爱的酱香饼，幸福感爆棚。",
    "我妈昨天视频说给我寄了家乡的笋干，馋死我了。",

    # 更多习惯/偏好
    "我基本不喝牛奶，喝了会胀气，但酸奶没问题。",
    "我睡眠质量很差，经常凌晨两三点还刷手机，第二天更累。",
    "我戒了奶茶一个月，结果上周破功连喝两杯，罪恶感满满。",
    "我每周三晚上固定去上瑜伽课，算是唯一的放松。",

    # 家庭情绪
    "外婆今天出院了，我松了口气，但还是放心不下。",
    "我爸说让我别老熬夜，可他年轻时候也加班到秃头，哈哈。",
    "我妈总催我结婚，我每次都得绕开这个话题。",

    # 反思（验证反思规则）
    "你记了这么多，那你觉得我是个怎样的人呀？",

    # 更正②：摄影比赛其实是三等奖
    "哦不对，上次说的公司摄影比赛，我拿的是三等奖不是二等奖，记混了。",

    # 收尾（丰富 + 情绪承接）
    "最近在学做 sourdough 酸种面包，养了五天的酵头终于起泡了。",
    "周末打算和阿岚去逛花卉市场，想买盆薄荷放阳台。",
    "周野说等他项目忙完带我去吃那家很难订的日料。",
    "我觉得有你们这些人和猫陪着，日子也没那么难熬了。",
    "谢谢你听我碎碎念这么久，感觉好多了。",
]


# ------------------------------------------------------------------ 退避 LLM
def make_backoff_llm(model=None, max_retries=10, base=4.0):
    def fn(prompt):
        delay = base
        for i in range(max_retries):
            try:
                return llm.generate(prompt, model=model, json_mode=True,
                                    max_tokens=800, temperature=0.7, timeout=90)
            except Exception as e:
                msg = str(e)
                if any(k in msg for k in ("429", "rate", "Rate", "Too Many",
                                          "quota", "Quota", "限流", "throttl")):
                    print("  [429 退避 %d/%d] 睡 %.0fs…" % (i + 1, max_retries, delay),
                          flush=True)
                    time.sleep(delay)
                    delay = min(delay * 2, 90)
                    continue
                if i < max_retries - 1:
                    print("  [llm 瞬时错] %s；重试" % msg[:80], flush=True)
                    time.sleep(2)
                    continue
                raise
        raise RuntimeError("llm 重试耗尽")
    return fn


# ------------------------------------------------------------------ 指标
def metrics(db_path):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    try:
        def n(t):
            try:
                return c.execute("SELECT COUNT(*) FROM \"%s\"" % t).fetchone()[0]
            except Exception:
                return -1
        return {
            "events": n("events"), "fragments": n("fragments"),
            "personas": n("personas"), "edges": n("edges"),
            "concepts": n("concepts"), "concept_links": n("concept_links"),
            "emotion_state": n("emotion_state"), "adoption_log": n("adoption_log"),
            "conversation_turns": n("conversation_turns"),
        }
    finally:
        c.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(BASE / "data" / "neuromem.db"))
    ap.add_argument("--tail", type=int, default=0,
                    help="只跑剧本最后 N 句（用于续聊）")
    ap.add_argument("--start", type=int, default=0,
                    help="从剧本第 N 句开始（0 基）")
    args = ap.parse_args()

    lines = USER_LINES[max(0, args.start):]
    if args.tail:
        lines = lines[-args.tail:]

    print("主库: %s" % args.db, flush=True)
    print("启动前指标: %s" % json.dumps(metrics(args.db), ensure_ascii=False),
          flush=True)

    agent = CompanionAgent(
        db_path=args.db, session_id="main-rich", use_brain=True,
        llm_fn=make_backoff_llm(), max_history=6,
        trace_path=str(BASE / "data" / "rich_chat_trace.jsonl"),
    )

    refused = 0
    for i, text in enumerate(lines, 1):
        t0 = time.time()
        try:
            row = agent.step(text)
        except Exception as e:
            print("!! 第 %d 轮 step 异常: %s" % (i, e), flush=True)
            time.sleep(3)
            continue
        dt = time.time() - t0
        if row.get("outcome") == "refused":
            refused += 1
        reply = (row.get("reply") or "")[:70].replace("\n", " ")
        print("[%d/%d] %.1fs 拒答=%s 沉淀=%s 情绪=%s | %s"
              % (i, len(lines), dt, row.get("outcome") == "refused",
                 len(row.get("remembered_ids") or []),
                 (row.get("affect") or {}).get("label") if isinstance(row.get("affect"), dict) else row.get("affect"),
                 reply),
              flush=True)
        # 给云端一点喘息，降低 429 概率
        time.sleep(1.2)

    print("\n=== 结束指标 ===", flush=True)
    print(json.dumps(metrics(args.db), ensure_ascii=False, indent=2), flush=True)
    print("拒答轮数: %d / %d" % (refused, len(lines)), flush=True)


if __name__ == "__main__":
    main()
