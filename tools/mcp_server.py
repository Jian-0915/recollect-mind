"""NeuroMem MCP server：把记忆暴露给 Claude Desktop / Cursor / 任意 MCP 客户端。

传输：stdio + JSON-RPC 2.0（MCP 2024-11-05 协议）
工具：
  memory_retrieve(query, top_k)      纯检索，返回可拼进 prompt 的紧凑文本
  memory_remember(text)              写入一条记忆
  memory_ask(query)                  检索 + 生成回答
  memory_person(name)                人物画像 + 关系
  memory_recent(days)                最近 N 天写入的记忆
  memory_status()                    库规模 / 阶段 / 评分
  memory_state()                     情绪基调 / 自叙事 / 画像 / 会话态
  memory_turn(text, speaker, ...)    记录一轮对话（会写 conversation_turns）
  memory_surface()                   无问句时主动浮现值得提起的记忆
  memory_feedback(used_ids, ...)     回传本轮真正采用了哪几条（L2，触发再巩固）

接入（Claude Desktop 的 claude_desktop_config.json）：
{
  "mcpServers": {
    "neuromem": {
      "command": "C:\\\\Users\\\\<你>\\\\AppData\\\\Local\\\\Programs\\\\Python\\\\Python313\\\\python.exe",
      "args": ["D:\\\\neuro-memory\\\\tools\\\\mcp_server.py"]
    }
  }
}
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import (companion, config, db, evaluation, goals, identity, persona,
                       persona_card, query, recall, store)  # noqa: E402

NAME = "neuromem"
VERSION = "1.0"

_TAG = {"verified": "确认", "inferred": "推测", "associative": "联想", "concept": "理解"}


def _conn():
    return db.connect()


def _when(it):
    occ = (it.get("occurred_at") or "").strip()
    if occ:
        return occ
    created = (it.get("created_at") or "").strip()
    return ("记于 " + created[:10]) if created else "日期不明"


def format_items(items, limit=8, with_ids=True):
    """带 id 输出：上层 agent 必须拿到 id 才能回传 used_ids（L2 采纳）。
    历史坑：旧版只输出「时间 + 标签 + 原话」，agent 根本无法知道采用了哪条，
    于是 adoption_log 永远只有 source=prompt 的 L1 弱信号。"""
    lines = []
    for it in items[:limit]:
        tag = _TAG.get(it.get("kind"), "")
        extra = []
        if it.get("place"):
            extra.append(it["place"])
        label = (it.get("affect") or {}).get("label")
        if label and label != "中性":
            extra.append(label)
        if it.get("fuzzy"):
            extra.append("印象模糊")
        if it.get("type") != "concept":
            # v12：让陪伴 agent 一眼看出这条记忆属于谁，而不是默认「都在讲对方」
            extra.append("关于" + identity.label_for(it.get("subject")))
        tail = ("（" + "，".join(extra) + "）") if extra else ""
        prefix = ("[id:%s] " % it["id"]) if with_ids and it.get("id") else ""
        lines.append("%s[%s][%s] %s%s" % (prefix, _when(it), tag, it["gist"], tail))
    return "\n".join(lines)


def tool_retrieve(args):
    q = (args.get("query") or "").strip()
    if not q:
        return "（需要 query）"
    k = int(args.get("top_k") or 8)
    c = _conn()
    st = None
    try:
        items = recall.recall(c, q, top_k=max(1, min(k, 20)), tag=args.get("tag"))
        subj = (args.get("subject") or "").strip()
        if subj in config.SUBJECTS:
            items = [x for x in items
                     if (x.get("subject") or config.DEFAULT_SUBJECT) == subj]
        rels = query._relation_context(c, q)
        goal_list = goals.active(c, limit=3)
        st = companion.state(c) if config.COMPANION_STATE else None
    finally:
        c.close()

    out = []
    if items:
        out.append("记忆：\n" + format_items(items, k))
        ids = [it["id"] for it in items[:k]
               if it.get("id") and it.get("type") != "concept"]
        if ids:
            out.append("依据 id（用上了请调 memory_feedback 回传）：" + "、".join(ids))
    else:
        out.append("记忆：（没有匹配）")
    if rels:
        out.append("关系：\n" + "\n".join(
            "- %s — %s — %s（强度 %.2f）" % (r["a"], r["type"], r["b"],
                                            float(r["strength"] or 0)) for r in rels[:5]))
    if goal_list:
        out.append("进行中的目标：\n" + "\n".join("- " + g["text"] for g in goal_list))
    if st:
        out.append("当前状态（语气与称呼参考，非事实）：\n" + companion.format_block(st))
    return "\n\n".join(out)


# F4（2026-11-15 长测后）：陪伴侧复述 / 工具痕迹不得沉淀成用户事实。
# 65 轮长测里出现过把「用户怕雷，年糕怕黄瓜，我们俩都怂」这类 Agent
# 口头禅写进 events，随后又被检索回来强化的污染闭环。
_AGENT_VOICE = re.compile(
    r"evt_[A-Za-z0-9_]+|依据|接住了|回复原话|本轮检索|未采用|未复述|used_ids|"
    r"source\s*=|\[(确认|推测|联想)\]|再巩固|陪伴侧|"
    r"(我们俩|咱俩|咱们俩)都怂|按不重复提同一件事"
)


def tool_remember(args):
    text = (args.get("text") or "").strip()
    if len(text) < 2:
        return "（需要 text）"
    hit = _AGENT_VOICE.search(text)
    if hit:
        return ("（拒绝写入：text 含陪伴侧复述或工具痕迹「%s」。"
                "memory_remember 只接受用户本轮原话事实。）" % hit.group(0))
    quote = (args.get("source_quote") or "").strip()
    if quote:
        _sq = lambda s: re.sub(r"[\s，,。.、]+", "", s or "")
        if _sq(text) not in _sq(quote):
            return "（拒绝写入：text 不是 source_quote 用户原话的子句，疑似合并或改写。）"
    c = _conn()
    try:
        ids = store.remember(c, text,
                              speaker=args.get("speaker") or config.COUNTERPART_NAME,
                              tags=args.get("tags"), subject=args.get("subject"))
    finally:
        c.close()

    # 与 HTTP 写入路径一致：写完立刻判断要不要补一轮代谢（D1）
    try:
        from neuromem import autoevolve
        autoevolve.maybe_trigger()
    except Exception:
        pass

    n, dup = store.LAST_WRITE.get("new", 0), store.LAST_WRITE.get("dup", 0)
    if not ids:
        return "内容里没有可记成事件的部分（寒暄或纯符号），未写入。"
    if dup and not n:
        return "这条记忆已存在，未重复写入（%s）" % "、".join(ids)
    if n and dup:
        return "新增 %d 条，另有 %d 条已存在：%s" % (n, dup, "、".join(ids))
    return "已记住 %d 条：%s" % (len(ids), "、".join(ids))


def tool_ask(args):
    q = (args.get("query") or "").strip()
    if not q:
        return "（需要 query）"
    c = _conn()
    try:
        r = query.query(c, q, top_k=8, with_summary=True, tag=args.get("tag"))
    finally:
        c.close()
    parts = []
    # v14：人设是人工设定的 L0，外部模型走 MCP 时也该看到同一份人格。
    if r.get("persona_block"):
        parts.append("人设（人工设定，不可被对话改写）：\n" + r["persona_block"])
    if r.get("state"):
        parts.append("当前状态：\n" + companion.format_block(r["state"]))
    if r.get("summary"):
        parts.append("回答：" + r["summary"])
    if r.get("events"):
        parts.append("依据：\n" + format_items(r["events"], 5))
        ids = [it["id"] for it in r["events"][:5]
               if it.get("id") and it.get("type") != "concept"]
        if ids:
            parts.append("本轮依据 id（用上了请调 memory_feedback 回传）：" + "、".join(ids))
    if r.get("warnings"):
        parts.append("提示：" + "；".join(r["warnings"]))
    return "\n\n".join(parts) or "（没有相关记忆）"


def tool_person(args):
    name = (args.get("name") or "").strip()
    if not name:
        return "（需要 name）"
    c = _conn()
    try:
        found = persona.find(c, name)
        if not found:
            return "没有找到人物「%s」" % name
        p = dict(found)
        gists = persona.event_gists(c, p["id"], limit=8)
        rels = [r for r in persona.list_relations(c)
                if r["a"] == p["canonical_name"] or r["b"] == p["canonical_name"]]
    finally:
        c.close()
    out = ["人物：%s" % p["canonical_name"]]
    if p.get("aliases_json") and p["aliases_json"] not in ("[]", ""):
        out.append("别名：%s" % p["aliases_json"])
    if p.get("profile"):
        out.append("画像：" + p["profile"].replace("\n", " "))
    if rels:
        out.append("关系：\n" + "\n".join(
            "- %s — %s — %s" % (r["a"], r["type"], r["b"]) for r in rels[:6]))
    if gists:
        out.append("相关记忆：\n" + "\n".join("- " + g[:40] for g in gists[:6]))
    return "\n".join(out)


def tool_recent(args):
    days = int(args.get("days") or 3)
    limit = min(int(args.get("limit") or 20), 100)
    offset = max(int(args.get("offset") or 0), 0)
    show_old = bool(args.get("include_superseded"))
    since = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
    where = "created_at>=?"
    if not show_old:
        where += " AND superseded_by IS NULL"   # 默认不显示已被更新的旧说法
    c = _conn()
    try:
        total = c.execute("SELECT COUNT(*) FROM events WHERE " + where, (since,)).fetchone()[0]
        rows = c.execute(
            "SELECT id,gist,occurred_at,created_at,salience,archived,superseded_by FROM events "
            "WHERE " + where + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (since, limit, offset)).fetchall()
    finally:
        c.close()
    if not rows:
        return "最近 %d 天没有新记忆" % days
    lines = []
    for r in rows:
        marks = []
        if r["archived"]:
            marks.append("已归档")
        if r["superseded_by"]:
            marks.append("已更新")
        tail = ("（" + "，".join(marks) + "）") if marks else ""
        lines.append("[%s][id:%s] %s%s" % (_when(dict(r)), r["id"], r["gist"], tail))
    more = ""
    if offset + len(rows) < total:
        more = "，还有 %d 条：offset=%d" % (total - offset - len(rows), offset + len(rows))
    return "最近 %d 天（共 %d 条，本次 %d 条%s）：\n" % (
        days, total, len(rows), more) + "\n".join(lines)


def tool_status(args):
    c = _conn()
    try:
        ov = {
            "events": c.execute("SELECT COUNT(*) FROM events WHERE archived=0").fetchone()[0],
            "archived": c.execute("SELECT COUNT(*) FROM events WHERE archived=1").fetchone()[0],
            "concepts": c.execute("SELECT COUNT(*) FROM concepts").fetchone()[0],
            "personas": c.execute("SELECT COUNT(*) FROM personas").fetchone()[0],
            "relations": c.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
        }
        ev = evaluation.evaluate(c)
        extra = ""
        if args.get("verbose"):
            try:
                g = c.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
                d = c.execute("SELECT COUNT(*) FROM drift_log").fetchone()[0]
                usage = []
                for t in ("adoption_log", "conversation_turns", "memory_versions"):
                    try:
                        usage.append("%s=%d" % (t, c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]))
                    except Exception:
                        pass
                try:
                    usage.append("agent回传=%d" % c.execute(
                        "SELECT COUNT(*) FROM adoption_log WHERE source='agent'").fetchone()[0])
                    usage.append("已再巩固=%d" % c.execute(
                        "SELECT COUNT(*) FROM events WHERE revision_count>0").fetchone()[0])
                except Exception:
                    pass
                extra = ("\n目标：%d 条 · 漂移记录：%d 条\n真实使用：%s"
                         % (g, d, " · ".join(usage)))
            except Exception:
                extra = ""
    finally:
        c.close()
    return ("记忆库：%d 条活跃（%d 归档）· %d 概念 · %d 人物 · %d 关系\n"
            "阶段：%s · 评分：%s" % (ov["events"], ov["archived"], ov["concepts"],
                                    ov["personas"], ov["relations"],
                                    ev.get("stage"), ev.get("score")) + extra)


def tool_state(args):
    c = _conn()
    try:
        st = companion.state(c)
        turns = companion.recent_turns(c, limit=int(args.get("turns") or 3))
        fixed = identity.block(c)          # L0 固定身份：每轮先看「我是谁」
        pblock = persona_card.brief(c)    # L0 人设卡短版
    finally:
        c.close()
    out = []
    if fixed:
        out.append("固定身份（人工设定，不可被对话改写）：\n" + fixed)
    if pblock:
        out.append("人设（人工设定，不可被对话改写）：\n" + pblock)
    out.append("当前状态：\n" + companion.format_block(st))
    if turns:
        out.append("最近对话：\n" + "\n".join(
            "- [%s] %s：%s" % (t["ts"][:16], t["speaker"],
                              (t["gist"] or t["text"] or "")[:60]) for t in turns))
    else:
        out.append("最近对话：（还没有记录）")
    return "\n\n".join(out)


def tool_turn(args):
    text = (args.get("text") or "").strip()
    if len(text) < 2:
        return "（需要 text）"
    c = _conn()
    try:
        tid = companion.remember_turn(
            c, args.get("speaker") or config.COUNTERPART_NAME, text,
            session_id=args.get("session_id"), gist=args.get("gist"),
            affect=args.get("affect"), event_ids=args.get("event_ids"),
            topic=args.get("topic"))
        if args.get("open_topic"):
            st = companion.state(c)
            topics = list(st.get("open_topics") or [])
            if args["open_topic"] not in topics:
                topics.append(args["open_topic"])
                companion.update_state(c, open_topics=topics[-10:])
        if args.get("remember"):
            store.remember(c, text,
                            speaker=args.get("speaker") or config.COUNTERPART_NAME,
                           subject=args.get("subject"))
    finally:
        c.close()
    return "已记录本轮对话（%s）%s" % (
        tid, "；并已沉淀为长期事实" if args.get("remember") else "")


def tool_surface(args):
    c = _conn()
    try:
        items = companion.surface(c, limit=int(args.get("limit") or 3))
    finally:
        c.close()
    if not items:
        return "（暂时没有适合主动提起的记忆）"
    return "此刻可以主动提起：\n" + "\n".join(
        "- [%.3f][id:%s] %s" % (it["score"], it.get("id", ""), it["gist"][:80])
        for it in items)


def tool_feedback(args):
    """L2 采纳回传：agent 明确告诉记忆层本轮真正用了哪几条。

    这是整条采纳闭环唯一由 agent 主动触发的入口。不调用就只有
    query() 自动写的 L1 弱信号：记忆不会因为「被用过」而变清晰，
    再巩固、版本链、复习效应全都拿不到输入。

    2026-11-15 长测后修复（F1 / F2）：
    - used_ids / rejected_ids 先按 events 表校验。幻觉 id 原先会原样写进
      adoption_log，污染「真实使用」指标与评测集（apply() 本就会跳过无效
      id，受损的是日志侧，不是 events 权重）。
    - 空 ids 不再提前 return，仍写一行日志，让 outcome=refused 可留痕。
      L1 的 auto_record 本来就支持 refused，这是 L2 独有的缺口。
    """
    def _ids(key):
        v = args.get(key) or []
        if isinstance(v, str):
            v = [x.strip() for x in v.replace("，", ",").split(",")]
        return [str(x).strip() for x in v if str(x).strip()]

    used, rejected = _ids("used_ids"), _ids("rejected_ids")
    query = (args.get("query_text") or "").strip()
    context = (args.get("context_text") or "").strip()
    outcome = args.get("outcome") or "answered"

    c = _conn()
    try:
        from neuromem import adoption

        def _valid(ids):
            ok, bad = [], []
            for eid in dict.fromkeys(ids):
                if c.execute("SELECT 1 FROM events WHERE id=?", (eid,)).fetchone():
                    ok.append(eid)
                else:
                    bad.append(eid)
            return ok, bad

        valid_used, bad_used = _valid(used)
        valid_rejected, bad_rejected = _valid(rejected)
        bad = bad_used + bad_rejected

        if not valid_used and not valid_rejected:
            # 没用到记忆（含拒答）也留一行；没有可强化的对象，故不调 apply()
            rid = adoption.record(
                c, query=query, source="agent", used_ids=[], rejected_ids=[],
                summary=context, outcome=outcome, weight=1.0)
            note = ("；已忽略 %d 个无效 id：%s" % (len(bad), "、".join(bad[:5]))) if bad else ""
            return "已记录空采纳 #%d（outcome=%s）%s；无 id 被强化。" % (rid, outcome, note)

        rid = adoption.record(
            c, query=query, source="agent", used_ids=valid_used,
            rejected_ids=valid_rejected, summary=context,
            outcome=outcome, weight=1.0)
        res = adoption.apply(c, used_ids=valid_used, rejected_ids=valid_rejected,
                             weak=False, context_text=context, query=query)
    finally:
        c.close()
    rc = res.get("reconsolidated")
    tip = ""
    if valid_used and not context:
        tip = "\n提示：未传 context_text，本次缺少重编码上下文，再巩固可能未生效。"
    if bad:
        tip += "\n提示：已忽略 %d 个不存在于 events 的 id：%s" % (len(bad), "、".join(bad[:5]))
    return ("已记录 L2 采纳 #%d：采用 %d 条、拒绝 %d 条；强化 %d 条%s%s"
            % (rid, len(valid_used), len(valid_rejected), res.get("strengthened", 0),
               ("；再巩固 %d 条（已写版本链）" % rc) if rc else "", tip))


TOOLS = [
    {
        "name": "memory_retrieve",
        "description": "从个人长期记忆中检索与问题相关的记忆、人物关系、进行中的目标。只检索不生成，适合给 agent 当上下文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索语句，例如「阿哲最近怎么样」"},
                "top_k": {"type": "integer", "description": "返回条数，默认 8", "default": 8},
                "tag": {"type": "string", "description": "只看某个主题标签（旅游/工作/家庭/健康/饮食/宠物/消费/居住/娱乐/情绪/社交/学习）"},
                "subject": {"type": "string",
                             "description": "只看某个主体的记忆：self(我) / counterpart(对方) / shared(我们) / third_party(第三方)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_remember",
        "description": "把一条信息写入长期记忆（自动抽取人物/时间/地点/情绪、去重、建关系）。只接受用户本轮原话里的稳定事实；含陪伴侧复述、工具痕迹或 id 引用的 text 会被拒绝，合并了多个主体的句子也会被拒。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "用户本轮原话里的稳定事实，一句一件事；不要写你的复述或总结"},
                "speaker": {"type": "string", "description": "说话人，默认「用户」"},
                "source_quote": {"type": "string",
                                 "description": "可选，本轮用户原话全文。传入后会校验 text 必须是它的子句，防止把改写/合并句写进库"},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "自定义标签（可选），如 [\"旅游\"]；不传则按内容自动打标"},
                "subject": {"type": "string",
                             "description": "这条记忆属于谁：self(我自己的承诺/反思) / counterpart(对方，默认) / shared(我们共同经历) / third_party(第三方)"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "memory_ask",
        "description": "检索并用记忆生成一句简短回答（会调用回答模型）。",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"},
                           "tag": {"type": "string", "description": "只看某个主题标签"}},
            "required": ["query"],
        },
    },
    {
        "name": "memory_person",
        "description": "查看某个人物的画像、关系和相关记忆。",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "memory_recent",
        "description": "查看最近 N 天写入的记忆。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 3},
                "limit": {"type": "integer", "default": 20, "description": "本次返回条数（≤100）"},
                "offset": {"type": "integer", "default": 0, "description": "续页偏移"},
                "include_superseded": {"type": "boolean", "default": False,
                                       "description": "是否包含已被更新（superseded）的旧说法"},
                "tag": {"type": "string", "description": "只看某个主题标签"},
            },
        },
    },
    {
        "name": "memory_status",
        "description": "查看记忆库规模、成长阶段与评分。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "verbose": {
                    "type": "boolean",
                    "description": "是否附带目标与漂移记录数（默认 false）",
                }
            },
            "required": ["verbose"],
            "additionalProperties": False,
        },
    },
    {
        "name": "memory_state",
        "description": "每轮对话开始前调用：先返回固定身份（我是谁 / 我服务谁 / 称呼与边界 / 语气 / 能力边界，人工设定不可改写），再读关系态（情绪基调、自叙事、画像、上次聊到哪、未闭合话题、称呼与边界）。关系态只作语气与称呼参考，不要当成事实复述。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "turns": {"type": "integer", "default": 3, "description": "附带最近几轮对话"},
            },
            "required": [],
        },
    },
    {
        "name": "memory_turn",
        "description": "每轮对话结束后调用：记录这一轮的原始对话（谁说的、说了什么），不写入长期事实库；需要沉淀事实时再传 remember=true 或另调 memory_remember。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "本轮对话原文"},
                "speaker": {"type": "string", "description": "说话人，默认「用户」"},
                "session_id": {"type": "string"},
                "gist": {"type": "string", "description": "一句话摘要"},
                "topic": {"type": "string", "description": "本轮话题，写入 last_topic"},
                "open_topic": {"type": "string", "description": "追加一个未闭合话题"},
                "affect": {"type": "object", "description": "本轮情绪，如 {valence, arousal, label}"},
                "event_ids": {"type": "array", "items": {"type": "string"}},
                "remember": {"type": "boolean", "default": False,
                             "description": "为 true 时同时把 text 抽成长期事实"},
                "subject": {"type": "string",
                             "description": "remember=true 时该事实属于谁：self / counterpart(默认) / shared / third_party"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "memory_surface",
        "description": "无提问时调用：按显著性 × 激活 × 近因，返回此刻适合主动提起的几条记忆，自动避开最近已提过的。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 3, "description": "返回条数"},
            },
            "required": [],
        },
    },
    {
        "name": "memory_feedback",
        "description": "每轮回答后调用：回传本轮真正采用了哪些记忆 id（必须从 memory_retrieve / memory_ask 返回的 [id:...] 原样抄，不存在的 id 会被丢弃并在返回里提示）。这是唯一触发采纳强化与再巩固的入口；不调用就只有 L1 弱信号。本轮没用到记忆或拒答时也要调用（空 used_ids + outcome=refused），现在会正常留一行日志。context_text 用于向量重编码；只有 query_text 会被写入关键词索引，因此不要在 query_text 里放你自己的复述。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "used_ids": {"type": "array", "items": {"type": "string"},
                             "description": "本轮真正写进回答或判断里用到的记忆 id"},
                "rejected_ids": {"type": "array", "items": {"type": "string"},
                                 "description": "明确判定不相关、不该再被召回的 id；没把握就留空"},
                "query_text": {"type": "string", "description": "本轮问句或触发检索的话"},
                "context_text": {"type": "string",
                                 "description": "你实际怎么复述、使用这件事的原话，用于定向重编码；不传则再巩固缺少上下文"},
                "outcome": {"type": "string", "description": "answered / refused / other"},
            },
            "required": [],
        },
    },
]

HANDLERS = {
    "memory_retrieve": tool_retrieve,
    "memory_remember": tool_remember,
    "memory_ask": tool_ask,
    "memory_person": tool_person,
    "memory_recent": tool_recent,
    "memory_status": tool_status,
    "memory_state": tool_state,
    "memory_turn": tool_turn,
    "memory_surface": tool_surface,
    "memory_feedback": tool_feedback,
}


def handle(req):
    method = req.get("method")
    rid = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": NAME, "version": VERSION},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments")
        # 兼容三种客户端：省略 / null / 传 JSON 字符串（某些客户端对无参工具会传 ""）
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except Exception:
                args = {}
        if not isinstance(args, dict):
            args = {}
        fn = HANDLERS.get(name)
        if not fn:
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": "未知工具：%s" % name}],
                               "isError": True}}
        try:
            text = fn(args)
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": text}], "isError": False}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": "执行失败：%s" % str(e)[:300]}],
                               "isError": True}}
    if method in ("notifications/initialized", "ping"):
        return None
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": "Method not found: %s" % method}}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        resp = handle(req)
        if resp is None:
            continue
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()