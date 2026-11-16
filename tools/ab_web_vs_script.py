"""对照：web 口径（带 live_web 历史）vs 脚本口径（空白会话）。

背景：用户反馈「我在 web 端测试的时候是没问题的」，而冒烟脚本里
「我开的什么车？」被答成「你上个月搬到城西了」。两者唯一的差别是
会话历史（web 是常驻 session=live_web，148 轮；脚本是全新 session）。

用法：python tools/ab_web_vs_script.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MAIN_DB = ROOT / "data" / "neuromem.db"

tmp = Path(tempfile.mkdtemp(prefix="abws_")) / "ab.db"
for suf in ("", "-wal", "-shm"):
    p = Path(str(MAIN_DB) + suf)
    if p.exists():
        shutil.copy2(p, str(tmp) + suf)
os.environ["NEUROMEM_DB"] = str(tmp)

from neuromem import config, settings  # noqa: E402

config.DB_PATH = tmp
settings.reload()

from agent.companion import CompanionAgent  # noqa: E402

QUERIES = ["我开什么车", "我喜欢什么", "除了吃的呢", "我上次去冰岛是什么时候？"]


def run(tag, session, maxlen):
    print("\n" + "=" * 62)
    print("%s  session=%s max_history=%d" % (tag, session, maxlen))
    print("=" * 62)
    with CompanionAgent(db_path=str(tmp), session_id=session,
                        trace_path=None, max_history=maxlen) as ag:
        print("回填历史 %d 条" % getattr(ag, "history_warmed", -1))
        for q in QUERIES:
            row = ag.step(q)
            th = row.get("think") or {}
            ids = row.get("used_ids") or []
            print("\n[%d] %s" % (row["turn"], q))
            print("    → %s" % row["reply"])
            print("    %dms | think=%s | 候选 %d 采用 %d | parse_ok=%s"
                  % (row["elapsed_ms"], th.get("reasoning_tokens"),
                     row["candidate_count"], len(ids), row["parse_ok"]))
            if ids:
                # 把采用的候选原文打出来，才能判断回答是不是"贴题"
                got = []
                for mid in ids:
                    try:
                        r = ag.brain.conn.execute(
                            "SELECT gist FROM fragments WHERE id=?", (mid,)).fetchone()
                        if r:
                            got.append(str(r[0])[:90])
                    except Exception:
                        pass
                for g in got:
                    print("      采用: %s" % g)


run("A. web 口径", "live_web", 10)
run("B. 脚本口径", "ab_blank_session", 8)
print("\n库副本：", tmp)
