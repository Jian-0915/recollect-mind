"""再巩固（reconsolidation）：被真正采用的记忆，按当时的上下文定向重编码。

人脑里「想起来」不是只读：每次提取都会让记忆进入可塑状态，被当前情境
轻微改写后再存回去。旧实现里 ``recall`` 只加 activation，``drift`` 又是在
后台随机给 5 条向量加高斯噪声 —— 前者不影响表示，后者与「是否被想起」
完全解耦，都不是再巩固。

这里做的是**有方向、可回溯、可限流**的版本：

- 只有 L2 显式 ``feedback(used_ids=...)`` 才触发；L1 自动信号只写日志。
- 把当时的问句/复述 embed 后按 α=0.12 并入原向量（保持模长），同时把
  旧 gist / 旧向量整份存进 ``memory_versions``，绝不丢历史。
- 同一条记忆 1 小时内最多重编码一次，版本链上限 12 版，防止长期对话
  把语义冲垮或把库写爆。
- ``search_text`` 只记录用户原话（query），让关键词通道命中改写过的问法；
  不再接收陪伴侧复述，避免语义串味与检索过热（F3，2026-11-15）。
- 嵌入服务不可用时静默跳过，绝不影响采纳与回答。
"""
import json
import math
import re
from datetime import datetime, timedelta, timezone

from . import config, db

_DDL = """
CREATE TABLE IF NOT EXISTS memory_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL,
  revision INTEGER,
  gist TEXT,
  embedding BLOB,
  affect_json TEXT,
  reason TEXT,
  query TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mv_event ON memory_versions(event_id, revision);
"""

_QWORDS = re.compile(
    r"请问|帮我|告诉我|我想知道|你记得|还记得|一下|什么|哪些|怎么样|怎样|"
    r"如何|是不是|有没有|多少|吗|呢|吧|[?？!！。，,、；;\s]+")

# F3（2026-11-15 长测后）：陪伴侧 context_text 常含工具痕迹与自我说明，
# 这些词一旦进入向量/索引就会变成「检索万能钥匙」，必须剔掉。
_META = re.compile(
    r"evt_[A-Za-z0-9_]+|依据|接住了|回复原话|本轮|未采用|未复述|used_ids|"
    r"source\s*=|\[(确认|推测|联想)\]|再巩固|陪伴侧|陪伴\s*AI|工具名|采纳回传")


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _parse(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt


def _ensure(conn):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_versions'"
    ).fetchone()
    if not row:
        conn.executescript(_DDL)
        conn.commit()


def clean_context(raw):
    """去掉问句壳与 Agent 元文本，留下可作为重编码语境的实词；太短则返回空串。"""
    t = _META.sub(" ", raw or "")
    t = _QWORDS.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) >= 4 else ""


def _blend(old, ctx, alpha):
    """保持原模长的定向平移：old ← normalize((1-α)·old + α·ctx)。"""
    if not old or not ctx or len(old) != len(ctx):
        return None
    norm = math.sqrt(sum(float(x) * float(x) for x in old)) or 1.0
    merged = [(1.0 - alpha) * float(a) + alpha * float(b)
              for a, b in zip(old, ctx)]
    mn = math.sqrt(sum(x * x for x in merged))
    if not mn:
        return None
    return [x * norm / mn for x in merged]


def reconsolidate(conn, used_ids, context_text=None, query=None, reason="adopted"):
    """对一批被采用的记忆做一次定向重编码。返回统计 dict，失败不抛异常。"""
    if not config.RECONSOLIDATION or not used_ids:
        return {"changed": 0, "skipped": "disabled"}
    text = clean_context(context_text or query or "")
    if not text:
        return {"changed": 0, "skipped": "no_context"}
    # F3：向量重编码仍用 context（需要当时的语境），但关键词索引只收用户原话。
    # 旧实现把 context_text 追加进 events.search_text，而 recall.py 把
    # search_text 与 gist 同等用于关键词打分 —— 陪伴侧复述因此成为永久
    # 检索入口，造成「采纳→更易召回→再采纳」的过热闭环。
    index_text = clean_context(query or "") if query else ""
    try:
        from . import embed as _embed
        qv = _embed.embed(text)
    except Exception as e:
        return {"changed": 0, "skipped": "embed", "error": str(e)[:160]}
    if not qv:
        return {"changed": 0, "skipped": "empty_vector"}

    _ensure(conn)
    now = datetime.now(timezone.utc).astimezone()
    stamp = now.isoformat(timespec="seconds")
    gap = timedelta(hours=config.RECONSOLIDATION_MIN_GAP_HOURS)
    changed, throttled, capped = 0, 0, 0
    for eid in dict.fromkeys(used_ids):
        row = conn.execute(
            "SELECT id,gist,embedding,affect_json,revision_count,"
            "last_reconsolidated_at,search_text FROM events WHERE id=?",
            (eid,)).fetchone()
        if not row:
            continue
        d = dict(row)
        rev = int(d.get("revision_count") or 0)
        if rev >= config.RECONSOLIDATION_MAX_REVISIONS:
            capped += 1
            continue
        last = _parse(d.get("last_reconsolidated_at"))
        if last and now - last < gap:
            throttled += 1
            continue
        old = db.unpack(d.get("embedding"))
        new = _blend(old, qv, config.RECONSOLIDATION_ALPHA)
        if new is None:
            continue
        conn.execute(
            "INSERT INTO memory_versions(event_id,revision,gist,embedding,affect_json,"
            "reason,query,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (eid, rev, d.get("gist"), d.get("embedding"), d.get("affect_json"),
             reason, (query or context_text or "")[:300], stamp))
        prev = d.get("search_text") or ""
        extra = index_text[:80]
        if extra and extra not in prev:
            # 按词去重，避免同一批高频词被反复堆叠（旧实现只做尾部截断）
            merged_text = " ".join(dict.fromkeys((prev + " " + extra).split()))[-300:]
        else:
            merged_text = prev
        conn.execute(
            "UPDATE events SET embedding=?, revision_count=?, "
            "consolidation_stage='reconsolidated', last_reconsolidated_at=?, "
            "search_text=? WHERE id=?",
            (db.pack(new), rev + 1, stamp, merged_text, eid))
        changed += 1
    if changed:
        conn.commit()
    return {"changed": changed, "throttled": throttled, "capped": capped,
            "context": text[:80]}


def versions(conn, event_id, limit=10):
    """一条记忆的历史版本（不含向量内容，供回放/审计）。"""
    _ensure(conn)
    rows = conn.execute(
        "SELECT id,revision,gist,reason,query,created_at FROM memory_versions "
        "WHERE event_id=? ORDER BY id DESC LIMIT ?", (event_id, int(limit))).fetchall()
    return [dict(r) for r in rows]


def revert(conn, event_id, revision=None):
    """回滚到某个历史版本；默认回到最早一版。返回是否成功。"""
    _ensure(conn)
    if revision is None:
        row = conn.execute(
            "SELECT * FROM memory_versions WHERE event_id=? ORDER BY id ASC LIMIT 1",
            (event_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM memory_versions WHERE event_id=? AND revision=? "
            "ORDER BY id ASC LIMIT 1", (event_id, int(revision))).fetchone()
    if not row:
        return False
    conn.execute(
        "UPDATE events SET gist=COALESCE(?,gist), embedding=COALESCE(?,embedding), "
        "consolidation_stage='reverted', last_reconsolidated_at=NULL WHERE id=?",
        (row["gist"], row["embedding"], event_id))
    conn.commit()
    return True
