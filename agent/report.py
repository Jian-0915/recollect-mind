"""真实使用数据验收：把「机制跑起来是什么效果」变成可测量的数字。

分两类：**对话行为**（来自 trace）与**库内状态**（来自 SQLite 只读查询）。
前者看 agent 有没有真的用记忆、有没有复读/编造；后者看 L2 采纳、再巩固、
情感、会话、插件这些机制有没有真的落库。对照组 use_brain=False 时库指标为空。
"""
import difflib
import sqlite3
from pathlib import Path


def _count(c, sql, args=()):
    try:
        return c.execute(sql, args).fetchone()[0]
    except sqlite3.Error:
        return None


def _ratio(a, b):
    return round(a / b, 3) if b else 0.0


def _trace_metrics(trace):
    n = len(trace)
    if not n:
        return {"turns": 0}
    replies = [r["reply"] for r in trace]
    used = sum(1 for r in trace if r["used_ids"])
    # 分母只统计「真正采用了记忆」的轮，分子必须用同一口径；否则
    # 寒暄轮也可能带 context_ok，算出 >1 的比例（实测 1.333）。
    ctx_ok = sum(1 for r in trace if r["used_ids"] and r["context_ok"])
    refused = sum(1 for r in trace if r["outcome"] == "refused")
    unanswered = sum(1 for r in trace
                     if not r["used_ids"] and r["outcome"] == "answered")
    repeats = sum(
        1 for a, b in zip(replies, replies[1:])
        if a and b and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8)
    leaked = sum(1 for r in trace
                 if "[id:" in r["reply"] or "候选" in r["reply"]
                 or "used_ids" in r["reply"])
    cand = sum(r["candidate_count"] for r in trace)
    hits = sum(len(r["used_ids"]) for r in trace)
    # v14 生成层护栏：只统计，不参与旧指标口径，缺字段的历史 trace 也能算。
    guard = [r for r in trace if r.get("fact_guard")]
    violations = sum(1 for r in trace if r.get("fact_violation"))
    return {
        "turns": n,
        "parse_ok_rate": _ratio(sum(1 for r in trace if r["parse_ok"]), n),
        "llm_error_turns": sum(1 for r in trace if r["llm_error"]),
        "avg_candidates": round(cand / n, 2),
        "avg_used_ids": round(hits / n, 2),
        "candidate_hit_rate": _ratio(hits, cand),
        "turns_with_used_memory": used,
        "context_text_rate_when_used": _ratio(ctx_ok, used),
        "refused_rate": _ratio(refused, n),
        "answered_without_basis": unanswered,
        "invalid_used_ids": sum(len(r["invalid_used_ids"]) for r in trace),
        "facts_remembered": sum(len(r["remembered_ids"]) for r in trace),
        "fact_write_errors": sum(len(r["fact_errors"]) for r in trace),
        "repeat_rate": _ratio(repeats, max(0, n - 1)),
        "label_leak_turns": leaked,
        # --- v14 护栏可观测性 ---
        "fact_guard_turns": len(guard),
        "fact_violation_turns": violations,
        "fact_rewrite_turns": sum(1 for r in trace if r.get("fact_rewritten")),
        "fact_trim_turns": sum(1 for r in trace if r.get("fact_trimmed")),
        "claims_missing_turns": sum(1 for r in trace if r.get("claims_missing")),
        "grounded_reply_rate": (_ratio(len(guard) - sum(
            1 for r in guard if r.get("fact_violation")), len(guard))
            if guard else None),
        "parse_retry_turns": sum(1 for r in trace if r.get("parse_retried")),
        "parse_retry_success": sum(1 for r in trace if r.get("parse_retry_ok")),
        "remember_fallback_turns": sum(1 for r in trace
                                       if r.get("remember_fallback")),
        "avg_latency_ms": int(sum(r["elapsed_ms"] for r in trace) / n),
        "mood_path": [r["mood_top"] or "-" for r in trace],
    }


def lab_report(db_path, trace=None, *, use_brain=True):
    """只读打开实验库，返回结构化报告；表/列缺失一律降级为 None。"""
    p = Path(db_path)
    out = {"db": str(p), "use_brain": bool(use_brain),
           "behavior": _trace_metrics(trace or []), "db_state": {}}
    if not p.exists():
        return out
    c = sqlite3.connect("file:%s?mode=ro" % p.as_posix(), uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        d = out["db_state"]
        d["schema_version"] = _count(c, "PRAGMA user_version")
        d["events"] = _count(c, "SELECT COUNT(*) FROM events")
        d["events_active"] = _count(c, "SELECT COUNT(*) FROM events WHERE archived=0")
        d["events_with_vector"] = _count(
            c, "SELECT COUNT(*) FROM events WHERE embedding IS NOT NULL")
        try:
            d["subject_distribution"] = {
                r[0]: r[1] for r in c.execute(
                    "SELECT subject,COUNT(*) FROM events GROUP BY subject")}
        except sqlite3.Error:
            d["subject_distribution"] = None
        d["fragments"] = _count(c, "SELECT COUNT(*) FROM fragments")
        d["adoption_agent"] = _count(
            c, "SELECT COUNT(*) FROM adoption_log WHERE source='agent'")
        d["adoption_with_used_ids"] = _count(
            c, "SELECT COUNT(*) FROM adoption_log WHERE source='agent' "
               "AND used_ids IS NOT NULL AND used_ids NOT IN ('','[]')")
        d["adoption_with_context"] = _count(
            c, "SELECT COUNT(*) FROM adoption_log WHERE source='agent' "
               "AND summary IS NOT NULL AND summary!=''")
        d["memory_versions"] = _count(c, "SELECT COUNT(*) FROM memory_versions")
        d["events_reconsolidated"] = _count(
            c, "SELECT COUNT(*) FROM events WHERE revision_count>0")
        d["conversation_turns"] = _count(
            c, "SELECT COUNT(*) FROM conversation_turns")
        d["affect_events"] = _count(c, "SELECT COUNT(*) FROM affect_events")
        d["affect_relations"] = _count(
            c, "SELECT COUNT(*) FROM affect_relations")
        try:
            r = c.execute("SELECT valence,arousal,energy FROM affect_state "
                          "WHERE id=1").fetchone()
            d["affect_state"] = dict(r) if r else None
        except sqlite3.Error:
            d["affect_state"] = None
        d["reflection_log"] = _count(c, "SELECT COUNT(*) FROM reflection_log")
        d["proactive_suggestions"] = _count(
            c, "SELECT COUNT(*) FROM proactive_suggestions")
        try:
            r = c.execute("SELECT version,updated_by FROM identity WHERE id=1").fetchone()
            d["identity"] = dict(r) if r else None
        except sqlite3.Error:
            d["identity"] = None
        try:
            from neuromem import identity as ident
            d["identity_verify"] = ident.verify(c)
        except Exception:
            d["identity_verify"] = None
    finally:
        c.close()
    return out


def format_report(d):
    """中文可读输出。"""
    b, s = d["behavior"], d["db_state"]
    L = []
    L.append("=" * 64)
    L.append("陪伴 Agent 真实使用报告  (%s)" % ("接记忆" if d["use_brain"] else "对照组·无记忆"))
    L.append("库：%s" % d["db"])
    L.append("=" * 64)
    L.append("【对话行为】")
    for k, label in (("turns", "轮次"), ("parse_ok_rate", "结构化输出成功率"),
                     ("llm_error_turns", "生成失败轮"),
                     ("avg_candidates", "平均候选数"),
                     ("avg_used_ids", "平均采用数"),
                     ("candidate_hit_rate", "候选命中率"),
                     ("turns_with_used_memory", "真正用到记忆的轮"),
                     ("context_text_rate_when_used", "采用时回传原话比例"),
                     ("facts_remembered", "沉淀事实数"),
                     ("refused_rate", "拒答率"),
                     ("answered_without_basis", "未采用记忆仍作答轮(含寒暄)"),
                     ("invalid_used_ids", "伪造 id 次数"),
                     ("repeat_rate", "相邻复读率"),
                     ("label_leak_turns", "标签泄露轮"),
                     ("fact_violation_turns", "事实违规轮(护栏)"),
                     ("fact_rewrite_turns", "  ├ 收敛重写轮"),
                     ("fact_trim_turns", "  └ 程序裁剪轮"),
                     ("parse_retry_success", "解析重试成功轮"),
                     ("remember_fallback_turns", "兜底提取事实轮"),
                     ("avg_latency_ms", "平均时延(ms)")):
        L.append("  %-22s %s" % (label, b.get(k, "-")))
    L.append("  情绪轨迹：%s" % " → ".join(b.get("mood_path", [])))
    if s:
        L.append("【库内状态】")
        for k, label in (("schema_version", "schema"), ("events", "事件总数"),
                         ("events_with_vector", "带向量事件"),
                         ("fragments", "碎片"),
                         ("subject_distribution", "主体分布"),
                         ("adoption_agent", "L2  agent 采纳"),
                         ("adoption_with_used_ids", "  ├ 带 used_ids"),
                         ("adoption_with_context", "  └ 带 context_text"),
                         ("memory_versions", "再巩固版本"),
                         ("events_reconsolidated", "被再巩固事件"),
                         ("conversation_turns", "会话轮次"),
                         ("affect_events", "情绪事件"),
                         ("affect_relations", "关系情感"),
                         ("affect_state", "当下情感状态"),
                         ("reflection_log", "反思日志"),
                         ("proactive_suggestions", "主动发起候选"),
                         ("identity", "固定身份版本"),
                         ("identity_verify", "身份校验")):
            L.append("  %-22s %s" % (label, s.get(k, "-")))
    else:
        L.append("【库内状态】对照组不写库（无数据）")
    return "\n".join(L)


def compare(report_a, report_b):
    """有记忆 vs 无记忆：只比可比的行为指标。"""
    a, b = report_a["behavior"], report_b["behavior"]
    keys = (("candidate_hit_rate", "候选命中率"),
            ("turns_with_used_memory", "用到记忆的轮"),
            ("refused_rate", "拒答率"),
            ("answered_without_basis", "无依据仍作答轮"),
            ("repeat_rate", "相邻复读率"),
            ("avg_latency_ms", "平均时延(ms)"))
    lines = ["=" * 64, "A/B 对照：接记忆 vs 不接记忆", "=" * 64]
    for k, label in keys:
        lines.append("  %-20s 接记忆 %-10s 无记忆 %s" %
                     (label, a.get(k, "-"), b.get(k, "-")))
    return "\n".join(lines)
