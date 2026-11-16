"""重建概念：清空旧概念后按新规则重新归纳（维护脚本）。

用法（建议先停服务，避免自动进化并发）：
    python service.py stop
    python tests/rebuild_concepts.py
    python service.py start
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import consolidator, db  # noqa: E402


def main():
    c = db.connect()
    old = c.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
    c.execute("DELETE FROM concept_links")
    c.execute("DELETE FROM concepts")
    c.execute("UPDATE events SET consolidation_stage='raw' WHERE archived=0")
    c.commit()
    print("清空旧概念 %d 个\n" % old)

    l1 = consolidator.run(c)
    print("L1 新建 %d 个" % len(l1))
    for x in l1:
        conf = c.execute("SELECT confidence FROM concepts WHERE id=?", (x["id"],)).fetchone()[0]
        print("  %.2f (%d条) %s" % (conf, x["n"], x["text"]))

    l2 = consolidator.run_concepts(c)
    print("\nL2 再抽象 %d 个" % len(l2))
    for x in l2:
        print("  [L%d] %s" % (x["level"], x["text"]))

    left = c.execute("SELECT COUNT(*) FROM events WHERE archived=0 AND id NOT IN "
                     "(SELECT event_id FROM concept_links)").fetchone()[0]
    print("\n未进概念的活跃事件：%d" % left)
    c.close()


if __name__ == "__main__":
    main()