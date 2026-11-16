"""采纳反馈：把「被召回」与「被采用」分开。

设计要点（2026-11-14）：
- `recall.py` 不再对候选无条件 reinforce —— 那是「曝光即强化」，会让负例反复
  检索出的噪声依据越来越强，甚至占据 drift 锚点。
- 问答链路用 L1 自动信号：只有真正进提示词的条目、且回答不是拒答时，
  才做收敛式弱强化 `amount = gain * (1 - activation)`（不会十次就饱和到 1.0）。
- 上层 agent 可用 `Memory.feedback(used_ids, rejected_ids)` 给 L2 强信号，
  按采纳强化、按拒绝降权，覆盖/补充 L1。
- 每次问答写一行 `adoption_log`，含候选 / 进 prompt / 采用 / 拒绝，
  让「哪一轮用了哪条记忆」可回放，也能攒成评测集。

日志写入失败绝不影响主问答流程。
"""
import json
from datetime import datetime

from . import config, forgetting

_DDL = """
CREATE TABLE IF NOT EXISTS adoption_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  query TEXT,
  source TEXT,
  candidate_ids TEXT,
  prompt_ids TEXT,
  used_ids TEXT,
  rejected_ids TEXT,
  summary TEXT,
  outcome TEXT,
  weight REAL
);
CREATE INDEX IF NOT EXISTS idx_adopt_ts ON adoption_log(ts);
"""

_REFUSAL = (
    "没有相关记忆", "没有关于", "没提到", "不知道", "记不清", "记不得",
    "说不清", "想不起来", "不清楚", "不确定", "找不到", "并没有",
    "未记录", "无相关", "没记录", "暂时说不清", "怕说错", "没有那段时间的记录",
)


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _dump(ids):
    return json.dumps(list(ids or []), ensure_ascii=False)


def _ensure(conn):
    """只在表缺失时执行 DDL；表已存在时仅做一次 sqlite_master 读，不抢写锁。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='adoption_log'"
    ).fetchone()
    if row:
        return
    conn.executescript(_DDL)
    conn.commit()


def looks_like_refusal(text):
    """L1 弱信号的粗判：拒答意味着这批依据没支撑住问题，不应当作被采用。"""
    t = text or ""
    return any(w in t for w in _REFUSAL)


def record(conn, *, query=None, source="prompt", candidate_ids=None,
           prompt_ids=None, used_ids=None, rejected_ids=None,
           summary=None, outcome=None, weight=1.0):
    """写一行采纳日志（不改 events）。返回 rowid。"""
    _ensure(conn)
    cur = conn.execute(
        "INSERT INTO adoption_log(ts,query,source,candidate_ids,prompt_ids,"
        "used_ids,rejected_ids,summary,outcome,weight) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (_now(), (query or "")[:500], source,
         _dump(candidate_ids), _dump(prompt_ids), _dump(used_ids),
         _dump(rejected_ids), (summary or "")[:500], outcome, float(weight)),
    )
    conn.commit()
    return cur.lastrowid


def apply(conn, used_ids=None, rejected_ids=None, *, weak=False,
          context_text=None, query=None):
    """按采纳结果更新 events.activation / recall_count，并触发再巩固。

    weak=True  → L1 自动，增益小；weak=False → L2 显式回传，增益大，
    且会把当时的上下文定向并入记忆表示（见 reconsolidation）。
    未进 prompt 的候选不降权（它们可能只是被截断的好记忆，不该被惩罚）；
    只有 agent 明确列入 rejected_ids 的才降权。
    """
    _ensure(conn)
    gain = config.ADOPT_WEAK_GAIN if weak else config.ADOPT_STRONG_GAIN
    used = list(dict.fromkeys(used_ids or []))
    used_set = set(used)
    rejected = [i for i in dict.fromkeys(rejected_ids or []) if i not in used_set]

    strengthened = 0
    for eid in used:
        row = conn.execute("SELECT activation FROM events WHERE id=?", (eid,)).fetchone()
        if not row:
            continue
        act = float(row["activation"] if row["activation"] is not None else 1.0)
        amount = gain * (1.0 - act)
        if amount <= 1e-6:
            continue
        forgetting.reinforce(conn, eid, amount=amount)
        strengthened += 1

    weakened = 0
    for eid in rejected:
        cur = conn.execute(
            "UPDATE events SET activation=MAX(?, COALESCE(activation,1.0)-?) WHERE id=?",
            (config.ADOPT_MIN_ACTIVATION, config.ADOPT_REJECT_PENALTY, eid))
        weakened += cur.rowcount
    conn.commit()
    out = {"strengthened": strengthened, "weakened": weakened}
    # 再巩固只发生在 L2：L1 自动信号若也改向量，普通问答会把表示冲垮。
    if not weak and config.RECONSOLIDATION and used:
        try:
            from . import reconsolidation as _rc
            rc = _rc.reconsolidate(conn, used, context_text=context_text,
                                   query=query)
            out["reconsolidated"] = rc.get("changed", 0)
            out["reconsolidation"] = rc
        except Exception as e:
            out["reconsolidation_error"] = str(e)[:200]
    return out


def auto_record(conn, *, query=None, items=None, summary=None, trace=None):
    """问答链路的 L1：写日志 + 弱强化。异常静默，绝不打断回答。"""
    if not config.ADOPT_AUTO:
        return {"adopted": [], "outcome": "disabled"}
    try:
        candidates = [it.get("id") for it in (items or []) if it.get("id")]
        prompt_ids = list((trace or {}).get("adopted_ids") or [])
        refused = looks_like_refusal(summary)
        outcome = "refused" if refused else "answered"
        effective = [] if refused else prompt_ids
        record(conn, query=query, source="prompt",
               candidate_ids=candidates, prompt_ids=prompt_ids,
               used_ids=effective, summary=summary, outcome=outcome,
               weight=config.ADOPT_WEAK_GAIN)
        res = apply(conn, used_ids=effective, weak=True)
        return {"outcome": outcome, "candidates": len(candidates),
                "prompt": len(prompt_ids), "adopted": effective, **res}
    except Exception as e:
        return {"outcome": "error", "adopted": [], "error": str(e)[:200]}


def recent(conn, limit=10):
    """最近几轮采纳记录，供 agent / 调试回看。"""
    _ensure(conn)
    rows = conn.execute(
        "SELECT id,ts,query,source,outcome,candidate_ids,prompt_ids,used_ids,"
        "rejected_ids,summary FROM adoption_log ORDER BY id DESC LIMIT ?",
        (int(limit),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("candidate_ids", "prompt_ids", "used_ids", "rejected_ids"):
            try:
                d[k] = json.loads(d[k] or "[]")
            except (ValueError, TypeError):
                d[k] = []
        out.append(d)
    return out
