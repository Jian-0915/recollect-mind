"""web 陪伴对话通道：把 web 上的对话真正接进记忆闭环。

和 ``/api/ask`` 的分工（两者都保留，不互相替代）：

    /api/ask   = 只读召回问答。给「查一下你记得什么」，不写会话轮次、
                 不沉淀新事实、不动情感与会话时钟。
    /api/live  = 陪伴 agent 通道。走真正的 CompanionAgent：
                 Brain.turn() → 生成 → 事实护栏 → Brain.remember()
                 → Brain.commit()（L2 采纳 + 再巩固 + 会话轮次 + 情感 + 插件）
                 所以「它问 → 你答 → 它记住 → 下一轮它真能想起」才闭环。

安全边界（写主库的受控入口，不是全面放开）：

1. **绝不碰 L0**：identity 宪法与 persona_card 人设卡只读。称呼一律用 L0
   的 ``call_user``，不接受前端传值——否则前端一个「用户」就能把人格地基
   写成另一套称呼。
2. **单写者**：一个进程内只有一个 CompanionAgent，靠模块级锁串行。Brain
   内部共用连接，并发写只会互相抢锁，不如排队。
3. **异常自愈**：任何一轮抛错就丢弃当前实例并置空，下一轮重建。连接被
   清库/换库搞坏时不会永久失联。
4. **可整体关闭**：``NEUROMEM_LIVE_WEB=0`` 后端点只返回说明，不影响 chat
   其他接口。
5. **可观测**：每轮追加一行到 ``data/live_web.trace.jsonl``（不用
   ``CompanionAgent.save_trace``，那个是全量覆盖写，会越跑越慢）。

trace 里的 ``remember`` 是本轮真正落库的事实，前端把它显示出来——
「机制在背后做了什么」必须能看见，否则用户感觉不到区别。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from . import config

ENABLED = os.environ.get("NEUROMEM_LIVE_WEB", "1") != "0"
MAX_INPUT = 2000


def _trace_path():
    """trace 跟着**库**走，不是跟着 data 目录走。

    原来固定 ``data/live_web.trace.jsonl``，于是拿副本库跑端到端时，
    测试对话全写进了主库的 trace——事后看日志会把测试当成用户的真实行为，
    排查方向直接跑偏（踩过一次）。主库仍用原名，不动历史。
    """
    env = os.environ.get("NEUROMEM_LIVE_TRACE")
    if env:
        return Path(env)
    dbp = Path(config.DB_PATH)
    if dbp.name == "neuromem.db":
        return dbp.parent / "live_web.trace.jsonl"
    return dbp.parent / (dbp.stem + ".live.trace.jsonl")


TRACE_PATH = _trace_path()

_lock = threading.Lock()
_agent = None
_stats = {"turns": 0, "errors": 0, "last_at": None, "last_ms": 0,
          "born_at": None}


def _spawn():
    """建一个指向当前库的陪伴 agent（延迟导入，避免与 neuromem 循环依赖）。"""
    from agent.companion import CompanionAgent
    return CompanionAgent(
        db_path=str(config.DB_PATH),
        session_id="live_web",
        trace_path=None,          # trace 由本模块追加写
        max_history=10,
    )


def _ensure():
    global _agent
    if _agent is None:
        _agent = _spawn()
        _stats["born_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return _agent


def _drop():
    global _agent
    if _agent is not None:
        try:
            _agent.close()
        except Exception:
            pass
    _agent = None


def _append_trace(row):
    """追加一行精简 trace；写失败不影响对话本身。"""
    try:
        slim = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "turn": row.get("turn"),
            "user": row.get("user"),
            "reply": row.get("reply"),
            "candidates": row.get("candidate_count"),
            "used_ids": row.get("used_ids"),
            "remember": row.get("remember"),
            "remembered_ids": row.get("remembered_ids"),
            "outcome": row.get("outcome"),
            "parse_ok": row.get("parse_ok"),
            "parse_retried": row.get("parse_retried"),
            "fact_violation": row.get("fact_violation"),
            "fact_rewritten": row.get("fact_rewritten"),
            "fact_trimmed": row.get("fact_trimmed"),
            "remember_fallback": row.get("remember_fallback"),
            "initiate": row.get("initiate"),
            "mood_top": row.get("mood_top"),
            "elapsed_ms": row.get("elapsed_ms"),
            "llm_error": row.get("llm_error"),
            # 思考开销：0 才算真关掉了。留在 trace 里，方便一眼看出端点有没有
            # 偷偷把思考打开（关它的参数并非每家都认）。
            "think_tokens": (row.get("think") or {}).get("reasoning_tokens"),
        }
        TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRACE_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(slim, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _hint(row):
    """把本轮机制动作翻成一句人话，前端直接显示。

    只报**真发生**的事：判重合并与新增必须分开说，否则"写入 1 条"会让人以为
    记忆里多了一条，实际只是把新说法并进了旧事件。
    """
    bits = []
    added = row.get("remember_added")
    deduped = row.get("remember_deduped")
    backdated = row.get("remember_backdated")
    if added is None:          # 老 trace 没有这组字段，退回按 id 数报
        if row.get("remembered_ids"):
            bits.append("写入记忆 %d 条" % len(row["remembered_ids"]))
    else:
        if added:
            bits.append("新增记忆 %d 条" % added)
        if deduped:
            bits.append("合并到已有记忆 %d 条" % deduped)
        if backdated:
            bits.append("补录旧事实 %d 条（不覆盖当前）" % backdated)
    if row.get("remember_fallback"):
        bits.append("兜底提取")
    if row.get("used_ids"):
        bits.append("引用旧记忆 %d 条" % len(row["used_ids"]))
    if row.get("fact_violation"):
        bits.append("事实护栏介入" +
                    ("（已重写）" if row.get("fact_rewritten") else "（已裁剪）"))
    if row.get("parse_retried"):
        bits.append("解析重试")
    return " · ".join(bits)


def status():
    """通道与库的现状。给前端一个"它现在记得多少"的把手。"""
    from . import companion as comp
    from . import db

    info = {"enabled": ENABLED, "loaded": _agent is not None,
            "db": str(config.DB_PATH), "trace": str(TRACE_PATH)}
    info.update(dict(_stats))
    try:
        c = db.connect()
        try:
            st = comp.state(c)
            info["last_turn_at"] = st.get("last_turn_at")
            info["open_topics"] = len(st.get("open_topics") or [])
            info["turns_stored"] = c.execute(
                "SELECT COUNT(*) FROM conversation_turns").fetchone()[0]
            info["events"] = c.execute(
                "SELECT COUNT(*) FROM events WHERE archived=0").fetchone()[0]
        finally:
            c.close()
    except Exception as e:
        info["db_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
    return info


def turn(text):
    """跑一轮「真记忆」对话。

    返回字段刻意收窄到前端要用的：reply / remembered / used / mood / hint。
    不返回 candidate 明细，避免把内部召回结构漏到界面上。
    """
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "empty", "hint": "说点什么再发吧"}
    if not ENABLED:
        return {"ok": False, "error": "disabled",
                "hint": "陪伴通道已关闭（NEUROMEM_LIVE_WEB=0），"
                        "可用只读问答查记忆"}
    if len(text) > MAX_INPUT:
        text = text[:MAX_INPUT]

    # Brain 单写者：串行排队，别让两个回合抢库。等太久就明确拒掉，
    # 而不是让前端以为卡死（一轮 LLM 生成可能 20~60 秒）。
    if not _lock.acquire(timeout=8):
        return {"ok": False, "error": "busy",
                "hint": "上一句还在想，等它答完再说"}
    try:
        t0 = time.time()
        try:
            row = _ensure().step(text)
        except Exception as e:
            _stats["errors"] += 1
            _drop()                  # 连接可能已坏，下一轮重建
            return {"ok": False,
                    "error": "%s: %s" % (type(e).__name__, str(e)[:200]),
                    "hint": "通道已重置，可以再发一次"}
        _stats["turns"] += 1
        _stats["last_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _stats["last_ms"] = row.get("elapsed_ms") or int((time.time() - t0) * 1000)
        _append_trace(row)
        return {
            "ok": True,
            "reply": row.get("reply") or "",
            "remembered": row.get("remember") or [],
            # 细分：新增 / 合并到已有 / 补录作废。只报"写入 N 条"会把判重合并
            # 也算成新增，用户看到 events 没变会觉得系统在骗他。
            "remembered_count": (row.get("remember_added")
                                 if row.get("remember_added") is not None
                                 else len(row.get("remembered_ids") or [])),
            "remembered_merged": row.get("remember_deduped") or 0,
            "remembered_backdated": row.get("remember_backdated") or 0,
            "used": len(row.get("used_ids") or []),
            "candidates": row.get("candidate_count") or 0,
            "outcome": row.get("outcome"),
            "parse_ok": row.get("parse_ok"),
            "fact_guard": row.get("fact_guard"),
            "fact_violation": row.get("fact_violation"),
            "fact_rewritten": row.get("fact_rewritten"),
            "fact_trimmed": row.get("fact_trimmed"),
            "remember_fallback": row.get("remember_fallback"),
            "mood": row.get("mood_top") or "",
            # 冷战期透出脸色：前端据此显示「还在生气」，也让这段行为可解释。
            "sulk": row.get("sulk") or {},
            "hint": _hint(row),
            "elapsed_ms": row.get("elapsed_ms"),
            "turn": row.get("turn"),
        }
    finally:
        try:
            _lock.release()
        except RuntimeError:
            pass


def reset():
    """丢弃当前实例（后台换人设后要让下次对话重新读 L0）。"""
    with _lock:
        _drop()
    return {"ok": True}
