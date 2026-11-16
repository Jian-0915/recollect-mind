"""关思考后的真实对话冒烟：跑几轮，看延迟、思考 token、回答贴不贴题。

同样只在主库副本上跑（会话会写库）。
用法：python tools/smoke_live_nothink.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MAIN_DB = ROOT / "data" / "neuromem.db"

tmp = Path(tempfile.mkdtemp(prefix="nothink_")) / "smoke.db"
for suf in ("", "-wal", "-shm"):
    p = Path(str(MAIN_DB) + suf)
    if p.exists():
        shutil.copy2(p, str(tmp) + suf)
os.environ["NEUROMEM_DB"] = str(tmp)

from neuromem import config, settings  # noqa: E402

config.DB_PATH = tmp
settings.reload()

from agent.companion import CompanionAgent  # noqa: E402

TURNS = ["你好呀", "我开的什么车？", "我上次去冰岛是什么时候？",
         "我想去哪里旅行", "我养了什么宠物"]

# 口径必须对齐 web：`/api/live` 是常驻 session=live_web + max_history=10，
# 提示词里带着历史。用全新空白 session 跑，等于换了个更难的题，得到的
# 「回答无关」不能当成 web 端现象（2026-11-16 就在这上面误判过一次）。
BLANK = "--blank" in sys.argv
SESSION = "smoke_blank" if BLANK else "live_web"
print("口径：%s（session=%s，历史 %d 条）"
      % ("空白会话（对照）" if BLANK else "web 端",
         SESSION, 0 if BLANK else 10))

with CompanionAgent(db_path=str(tmp), session_id=SESSION,
                    trace_path=None, max_history=8 if BLANK else 10) as ag:
    for q in TURNS:
        row = ag.step(q)
        th = row.get("think") or {}
        print("\n[%d] %s" % (row["turn"], q))
        print("    → %s" % row["reply"])
        print("    %dms | think=%s tok(off_honored=%s) | 候选 %d 采用 %d | parse_ok=%s"
              % (row["elapsed_ms"], th.get("reasoning_tokens"),
                 th.get("off_honored"), row["candidate_count"],
                 len(row["used_ids"]), row["parse_ok"]))
        if not row["parse_ok"]:
            print("    raw: %r" % (str(row.get("raw_head"))[:300],))
