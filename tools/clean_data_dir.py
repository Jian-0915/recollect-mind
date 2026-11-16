"""data/ 目录整理：把「实验产物」和「记忆数据」分开。

背景：跑一次 A/B 实验就在 data/ 下留一个 11MB 的 db 副本（含 2560 维向量，
gzip 只压得掉 20%），一年多攒到 303MB / 31 个副本。真正的记忆只有
``neuromem.db`` 一个文件，其余都是可以重跑脚本再生的中间态。

保留：
  neuromem.db                 主库
  neuromem_main_*.db          主库历史快照（最近 2 个）
  backups/                    备份
  eval_*.json                 评测结果（用来对比版本）

归档到 _experiments/：
  *_report.json *_comparison.json *.trace.jsonl  *.log  ui*.png

删除（可重跑再生）：
  _probe_*.db  ab_*.db  fixcheck_*.db  longtest_*.db  agent_lab*.db
  pronoun_test.db  decay_test.db  rel_guard.db  _episode_vecs.json
  以及它们的 -shm / -wal 孤儿

用法：
    python tools/clean_data_dir.py            # 只报告
    python tools/clean_data_dir.py --apply
"""
import argparse
import glob
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import config  # noqa: E402

KEEP_DB = {"neuromem.db"}
# 主库快照：只留最近 N 个
KEEP_SNAPSHOTS = 2
SNAPSHOT_GLOB = "neuromem_main_*.db"

DISPOSABLE_DB = (
    "_probe_*.db", "ab_*.db", "fixcheck_*.db", "longtest_*.db",
    "agent_lab*.db", "pronoun_test.db", "decay_test.db", "rel_guard.db",
)
ARCHIVE_MOVE = (
    "*_report.json", "*_comparison.json", "*.trace.jsonl", "*.log",
    "ui*.png", "_cmp.py", "dump.txt", "_episode_vecs.json",
)


def _mb(n):
    return "%.1fMB" % (n / 1e6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    root = str(config.DATA_DIR)
    exp = os.path.join(root, "_experiments")
    before = sum(os.path.getsize(os.path.join(root, f))
                 for f in os.listdir(root)
                 if os.path.isfile(os.path.join(root, f)))

    snapshots = sorted(
        [os.path.join(root, f) for f in glob.glob(SNAPSHOT_GLOB)],
        key=os.path.getmtime, reverse=True)
    stale_snap = snapshots[KEEP_SNAPSHOTS:]

    doomed = []
    for pat in DISPOSABLE_DB:
        doomed.extend(glob.glob(os.path.join(root, pat)))
    for f in list(doomed):
        for suf in ("-shm", "-wal"):
            if os.path.exists(f + suf):
                doomed.append(f + suf)
    # 已被删掉的库留下的孤儿 WAL
    for f in glob.glob(os.path.join(root, "*.db-shm")) + glob.glob(os.path.join(root, "*.db-wal")):
        base = f.rsplit("-", 1)[0]
        if not os.path.exists(base) and f not in doomed:
            doomed.append(f)

    moves = []
    for pat in ARCHIVE_MOVE:
        moves.extend(glob.glob(os.path.join(root, pat)))

    doomed = [p for p in dict.fromkeys(doomed) if os.path.basename(p) not in KEEP_DB]
    moves = [p for p in dict.fromkeys(moves) if p not in doomed]

    freed = sum(os.path.getsize(p) for p in doomed if os.path.exists(p))
    print("可删除（实验副本，可重跑再生）：%d 个，%s" % (len(doomed), _mb(freed)))
    for p in doomed[:40]:
        print("   - %s" % os.path.basename(p))
    if len(doomed) > 40:
        print("   …另有 %d 个" % (len(doomed) - 40))
    print("旧快照（保留最近 %d 个）：%d 个" % (KEEP_SNAPSHOTS, len(stale_snap)))
    for p in stale_snap:
        print("   - %s" % os.path.basename(p))
    print("归档到 _experiments/：%d 个" % len(moves))

    if not a.apply:
        print("\n[dry] 未改动。加 --apply 执行。")
        return
    for p in doomed + stale_snap:
        try:
            os.remove(p)
        except OSError as e:
            print("  删除失败 %s: %s" % (p, e))
    os.makedirs(exp, exist_ok=True)
    for p in moves:
        try:
            shutil.move(p, os.path.join(exp, os.path.basename(p)))
        except OSError as e:
            print("  归档失败 %s: %s" % (p, e))
    after = sum(os.path.getsize(os.path.join(root, f))
                for f in os.listdir(root)
                if os.path.isfile(os.path.join(root, f)))
    print("\n顶层文件：%s → %s" % (_mb(before), _mb(after)))


if __name__ == "__main__":
    main()
