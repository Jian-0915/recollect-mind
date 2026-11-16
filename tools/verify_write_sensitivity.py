"""写入敏感度回归：验证「刚说过的话，下一轮就能接上」。

复现的是 2026-11-16 主库真实对话里的失败：

    09:18:44 你：金毛今天撞我的腿，可爱死了      → remember=[]（没写）
    09:19:28 你：金毛今天干嘛了                  → 它说「我好像没记着」

根因两条：① 写不写完全由模型判断「值不值得长期记」，当天小事被丢掉；
② 兜底只白名单「上周/昨天/上次」，「今天」不在里面，所以兜底也没触发。

用法（不碰主库，跑在副本上）：

    python tools/verify_write_sensitivity.py            # 真模型，约 2~4 分钟
    python tools/verify_write_sensitivity.py --dry-run   # 只打印剧本
"""
import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

# 剧本：(用户说的, 期望本轮写入, 期望下一句能接上的关键词)
SCRIPT = [
    ("金毛今天撞我的腿，可爱死了", True, None),
    ("金毛今天干嘛了", False, "撞"),
    ("2026年8月我去了趟大理，住了五天", True, None),
    ("我 8 月去哪了", False, "大理"),
    ("我最近在学篆刻，刻了方闲章送自己", True, None),
    ("我最近在学什么来着", False, "篆刻"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(BASE / "data" / "neuromem.db"))
    args = ap.parse_args()

    if args.dry_run:
        for say, want_write, want_recall in SCRIPT:
            print("%-30s 期望写入=%-5s 期望接上=%s" % (say, want_write, want_recall))
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="wm_sens_")) / "probe.db"
    shutil.copy2(args.db, tmp)
    for suffix in ("-wal", "-shm"):
        src = Path(str(args.db) + suffix)
        if src.exists():
            shutil.copy2(src, Path(str(tmp) + suffix))

    os.environ["NEUROMEM_DB"] = str(tmp)
    from agent.companion import CompanionAgent

    passed = failed = 0
    agent = CompanionAgent(db_path=str(tmp), session_id="sens_probe",
                           trace_path=None, max_history=10)
    try:
        for i, (say, want_write, want_recall) in enumerate(SCRIPT, 1):
            row = agent.step(say)
            wrote = bool(row.get("remembered_ids"))
            add = row.get("remember_added") or 0
            dup = row.get("remember_deduped") or 0
            print("\n[%d] 你：%s" % (i, say))
            print("    它：%s" % row.get("reply"))
            print("    remember=%s 新增=%d 合并=%d 兜底=%s 复读重试=%s"
                  % (row.get("remember"), add, dup,
                     row.get("remember_fallback"), row.get("repeat_retried")))
            if want_write is not None:
                ok = wrote == want_write
                print("    写入判定：%s（期望 %s）" % ("PASS" if ok else "FAIL",
                                                "有写入" if want_write else "不写入"))
                passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
            if want_recall:
                ok = want_recall in str(row.get("reply") or "")
                print("    接上一句：%s（回复里要有「%s」）"
                      % ("PASS" if ok else "FAIL", want_recall))
                passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
            if row.get("fact_violation"):
                print("    ! 事实护栏介入 rewritten=%s trimmed=%s"
                      % (row.get("fact_rewritten"), row.get("fact_trimmed")))
    finally:
        agent.close()

    print("\n结果：%d passed, %d failed  （副本库 %s）" % (passed, failed, tmp))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
