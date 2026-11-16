"""清理模板生成的荒诞记忆（人物与场景随机交叉导致），并清理其派生关系。

只删除**同时满足**两条的：① 最近 N 分钟内写入 ② 命中模板特征短语。
用户自己写的内容不会被碰到。

用法：
    python tests/prune_events.py --dry-run
    python tests/prune_events.py --minutes 60
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import db  # noqa: E402

# 强特征：模板特有、无信息量
STRONG = [
    "待了很久",
    "跟我说了一件家事",
    "在楼下的小馆子在楼下",
    "在小区花园在群里",
    "在菜市场看完",
    "在咖啡馆和客户吵架",
    "赶完一个拖了很久的方案",
    "排队两小时办事",
    "被领导当众批评",
    "翻到几年前的旧照片",
    "捡到一只小猫",
    "修好了水龙头",
    "拿到一笔意外的奖金",
]
_WEAK_RE = re.compile(
    r"^(今天|昨天|前天|上周|上上周|前一阵|上个月|\d+ 天前)"
    r"(我|用户|阿哲|小林|小周|妈妈|爸爸|王姨|奶奶|表哥|邻居老李|同事阿May)?在")


def is_template(gist):
    text = gist or ""
    if any(p in text for p in STRONG):
        return True
    return bool(_WEAK_RE.match(text))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=120)
    ap.add_argument("--after", default="", help="只处理该时间之后写入的，如 2026-11-12T14:20")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    c = db.connect()
    since = a.after or (datetime.now() - timedelta(minutes=a.minutes)).astimezone().isoformat(timespec="seconds")
    rows = c.execute("SELECT id,gist,created_at FROM events WHERE archived=0 AND created_at>=?",
                     (since,)).fetchall()
    hits = [r for r in rows if is_template(r["gist"])]
    print("窗口内事件 %d 条，命中模板 %d 条" % (len(rows), len(hits)))
    for r in hits[:8]:
        print("  - %s" % r["gist"][:44])

    if a.dry_run:
        print("\n（dry-run，不删除）")
        c.close()
        return

    ids = [r["id"] for r in hits]
    if not ids:
        c.close()
        return
    q = ",".join("?" * len(ids))
    # 先摘掉概念链接与绑定，再删事件
    c.execute("DELETE FROM concept_links WHERE event_id IN (%s)" % q, ids)
    c.execute("DELETE FROM bindings WHERE event_id IN (%s)" % q, ids)
    c.execute("DELETE FROM edges WHERE source IN (%s) OR (target IN (%s) AND target LIKE 'evt_%%')"
              % (q, q), ids + ids)
    c.execute("DELETE FROM events WHERE id IN (%s)" % q, ids)
    c.commit()

    left = c.execute("SELECT COUNT(*) FROM events WHERE archived=0").fetchone()[0]
    print("\n已删除 %d 条，剩余活跃事件 %d 条" % (len(ids), left))
    print("派生关系清理：请接着跑 python tests/rebuild_concepts.py")
    c.close()


if __name__ == "__main__":
    main()