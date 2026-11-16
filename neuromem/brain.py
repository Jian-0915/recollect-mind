"""Brain（v13）：陪伴 agent 的进程内总入口，取代「直接调 MCP 工具拼 prompt」。

契约（与 MCP 版本一致，但一次调用拿全）：

    brain = Brain()
    pack  = brain.turn("上次我们聊到哪儿了？")   # 大脑只给候选，不生成回答
    reply = agent.generate(pack.render())        # 由 agent 自己判断说不说、怎么说
    brain.commit(reply, used_ids=pack.candidate_ids_used_by_agent(),
                query_text="...", context_text="...")

- L0 固定身份永远在最前，agent 没有权限改写；
- 候选带 ``id`` / ``subject`` / ``perspective``，是否相关、是否说出口由 agent 判断；
- ``commit()`` 统一回写 L2 采纳（含再巩固）、会话轮次、多维情感、插件副作用；
- 不调用任何生成模型，不与 /api/ask、memory_ask 抢生成主体。

MCP 仍是外部模型兼容通道；陪伴 agent 自己走这个 SDK。
"""
from datetime import datetime, timezone

from . import (adoption, affect, companion, config, db, goals, identity,
               persona_card, query, recall, store)

_POLICY = (
    "【契约】候选只是候选，是否相关、是否说出口由你自己判断；"
    "检索不到就如实说不知道，不要把推测当事实，不要复述画像或自叙事；"
    "回答后必须回传 used_ids 与 context_text（你实际用上这件事的原话）。"
)


def _now():
    return datetime.now(timezone.utc).astimezone()


def _hours_since(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_now().tzinfo)
        return max(0.0, (_now() - d).total_seconds() / 3600.0)
    except (ValueError, TypeError):
        return None


class ContextPack(dict):
    """一轮对话的结构化上下文。既是 dict（可直接 JSON 给模型），也能 render 成文本。"""

    def candidate_ids(self):
        return [c.get("id") for c in self.get("candidates", []) if c.get("id")]

    def render(self, max_candidates=None):
        """紧凑文本：固定身份 → 提示 → 候选 → 情绪 → 关系/目标 → 插件笔记。"""
        lines = []
        # v14：人设段自带宪法底线，两者不重复输出；未设置人设时维持旧行为。
        if self.get("persona_block"):
            lines.append(self["persona_block"])
        elif self.get("identity_block"):
            lines.append(self["identity_block"])
        if self.get("hints"):
            lines.append("【本轮提示】\n- " + "\n- ".join(self["hints"]))
        cands = self.get("candidates") or []
        if max_candidates is not None:
            cands = cands[:max_candidates]
        if cands:
            lines.append("【候选记忆（未采用，需自己判断）】")
            for c in cands:
                # 聚合层给的是「全库统计 + 明细」，本身就是多条的集合，
                # 按单条截到 160 字会把明细全砍掉，等于白给。
                limit = 700 if c.get("type") == "rollup" else 160
                gist_text = (c.get("gist") or "")[:limit]
                if c.get("type") == "rollup":
                    lines.append("- [统计] %s（共 %s 条，可直接引用计数）"
                                 % (gist_text, c.get("count")))
                    continue
                # 归属标签只在它和句子主语不矛盾时才显示。实测「阿岚养了只狗叫
                # 布丁」被标成 subject=counterpart（关于对方），渲染成
                # 「[关于对方] 阿岚养了只狗叫布丁」——标签盖过了句子主语，
                # 模型于是答「你养了只金毛叫布丁」，把别人的狗算成了对方的。
                label = c.get("subject_label") or "对方"
                perspective = c.get("perspective") or ""
                if label == "对方" and not gist_text.startswith("你"):
                    # perspective 也在说「这条是关于对方的，回答里用『你』」，
                    # 是同一个误导的两处出口，必须一起关掉。
                    label, perspective = "", ""
                lines.append("- [id:%s] %s%s（%.3f）%s" % (
                    c.get("id"), "[关于%s] " % label if label else "",
                    gist_text, float(c.get("score") or 0),
                    " · " + perspective if perspective else ""))
        else:
            lines.append("【候选记忆】空（不要编造过去）")
        mood = self.get("mood") or {}
        if mood:
            lines.append("【当下状态】%s%s（能量 %.2f，效价 %.2f）" % (
                mood.get("top_label") or "平静",
                "" if not mood.get("top") else "·强度 %.2f" % mood.get("intensity", 0),
                float(mood.get("energy") or 0), float(mood.get("valence") or 0)))
        st = self.get("state") or {}
        if st.get("open_topics"):
            lines.append("【还没聊完】" + "、".join(
                str(x)[:40] for x in st["open_topics"][:3]))
        if self.get("goals"):
            lines.append("【进行中的目标】" + "、".join(
                str(g.get("text") or "")[:40] for g in self["goals"][:3]))
        notes = self.get("module_notes") or {}
        for name, note in notes.items():
            if note:
                lines.append("【%s】%s" % (name, note))
        lines.append(_POLICY)
        return "\n".join(lines)


class Brain:
    def __init__(self, path=None, session_id="default", counterpart=None,
                 modules=None):
        self.conn = db.connect(path)
        self.session_id = session_id or "default"
        # 后台人工设置的称呼优先；未设置时沿用旧常量，保证旧库 / 旧单测不变。
        self.counterpart = (counterpart or persona_card.call_user(self.conn)
                            or config.COUNTERPART_NAME)
        self._last_query = ""
        from . import cortex
        self.cortex = cortex.Cortex(self, modules=modules)

    # ---------- 一轮开始：只给上下文，不生成回答 ----------
    def turn(self, text, *, top_k=None, tag=None, affect_now=None):
        """返回 ContextPack。只读检索 + 状态投影；不写 events、不生成回答。"""
        q = (text or "").strip()
        self._last_query = q
        st = companion.state(self.conn)
        snap = affect.snapshot(self.conn)

        items = recall.recall(self.conn, q,
                              top_k=int(top_k or config.BRAIN_TOP_K), tag=tag)
        candidates = []
        for it in items:
            sub = it.get("subject") or config.DEFAULT_SUBJECT
            c = dict(it)
            if c.get("type") == "rollup":
                # 统计候选是「集合本身」，没有第一/第二人称归属，套主体标签
                # 会把它渲染成「[关于对方] 全库共 12 条…」这种莫名其妙的东西。
                c["subject_label"] = ""
                c["perspective"] = ""
                candidates.append(c)
                continue
            c["subject"] = sub
            c["subject_label"] = identity.label_for(sub)
            c["perspective"] = identity.perspective_hint(sub)
            candidates.append(c)

        try:
            rels = query._relation_context(self.conn, q)
        except Exception:
            rels = []
        try:
            gl = goals.active(self.conn, limit=5)
        except Exception:
            gl = []

        idle = _hours_since(st.get("last_turn_at"))
        ok_init, why = affect.should_initiate(
            snap, idle, has_open_topic=bool(st.get("open_topics")))

        hints = list(affect.expression_hints(snap))
        if any(c["subject"] == "self" for c in candidates):
            hints.append("候选里有关于我自己（陪伴者）的记忆，不要说成对方的事。")
        if st.get("narrative") and st.get("narrative_scope") != "self":
            hints.append("库里的自述是关于对方的旧档案，不是我的人格，不要当自传复述。")
        if not candidates:
            hints.append("这一轮没检索到可用记忆；没依据就别硬凑。")

        try:
            pcur = persona_card.get(self.conn)
            pblock = persona_card.render(self.conn)
        except Exception:
            pcur, pblock = None, ""

        pack = ContextPack(
            query=q, session_id=self.session_id,
            identity=st.get("identity"), identity_block=st.get("identity_block") or "",
            persona=pcur, persona_block=pblock,
            state=st, mood=snap, candidates=candidates,
            relations=rels, goals=gl, hints=hints,
            initiate={"suggested": ok_init, "reason": why,
                      "idle_hours": idle},
            module_notes={}, policy=_POLICY,
        )
        self.cortex.on_turn_start(pack, affect_now=affect_now)
        pack["module_notes"] = self.cortex.contribute(pack)
        return pack

    # ---------- 一轮结束：统一回写 ----------
    def commit(self, reply, *, used_ids=None, rejected_ids=None, query_text=None,
               context_text=None, outcome="answered", affect_input=None,
               topic=None, open_topic=None, user_text=None):
        """L2 采纳 + 再巩固 + 会话轮次 + 多维情感 + 插件副作用，一次做完。"""
        q = (query_text or self._last_query or "").strip()
        used = list(dict.fromkeys(used_ids or []))
        rejected = list(dict.fromkeys(rejected_ids or []))

        rid = adoption.record(
            self.conn, query=q, source="agent", used_ids=used,
            rejected_ids=rejected, summary=context_text,
            outcome=outcome, weight=1.0)
        applied = adoption.apply(
            self.conn, used_ids=used, rejected_ids=rejected,
            weak=False, context_text=context_text, query=q)

        # 会话轮次：先对方、再我。affect 折成旧两维，让 remember_turn 顺带
        # 更新 emotion_state（既有召回偏置），避免与 affect.update 重复写两次。
        turn_affect = None
        if affect_input:
            v, a = affect.valence_arousal(affect_input)
            turn_affect = {"valence": v, "arousal": a}
        if user_text is not None:
            companion.remember_turn(
                self.conn, self.counterpart, user_text,
                session_id=self.session_id, affect=turn_affect,
                event_ids=used, topic=topic)
        tid = companion.remember_turn(
            self.conn, "agent", reply or "", session_id=self.session_id,
            affect=turn_affect, event_ids=used, topic=topic)
        if open_topic:
            st = companion.state(self.conn)
            topics = list(st.get("open_topics") or [])
            if open_topic not in topics:
                companion.update_state(self.conn,
                                       open_topics=(topics + [open_topic])[-10:])

        snap = affect.update(
            self.conn, affect_input, person=self.counterpart,
            trigger="commit", note=(reply or "")[:200], sync_emotion=False)

        result = {"log_id": rid, "adoption": applied, "turn_id": tid,
                  "affect": snap, "context_text_ok": bool(context_text)}
        self.cortex.on_commit(result, query=q, reply=reply or "", used_ids=used)
        return result

    # ---------- 显式写入 / 主动浮现 / 离线 tick ----------
    def remember(self, text, *, subject=None, affect_input=None, tags=None,
                 speaker=None):
        """写入一条事实；强情绪自动提高编码显著性（情绪作用于编码）。"""
        if affect_input is None:
            strength = affect.salience_boost(affect.snapshot(self.conn))
        else:
            vals = list(affect.parse_emotions(affect_input).values())
            strength = affect.salience_boost(
                {"intensity": max(vals) if vals else 0.0})
        if affect_input:
            affect.update(self.conn, affect_input, person=self.counterpart,
                          trigger="remember", note=(text or "")[:160])
        return store.remember(
            self.conn, text,
            speaker=speaker or self.counterpart,
            subject=subject, tags=tags,
            affect_strength=strength if strength > 0 else None)

    def surface(self, limit=3):
        return companion.surface(self.conn, limit=limit)

    # 刻意不提供 set_identity：Brain 是陪伴 agent 的接口，agent 只要能调到
    # 它就能伪造 actor='human' 改写宪法。L0 只允许经 Memory / HTTP / CLI
    # 的人工入口升级，agent 侧永远只有下面的只读身份视图。
    def identity(self):
        return identity.get(self.conn)

    def identity_block(self):
        return identity.block(self.conn)

    # 人设同属 L0：agent 侧只有只读视图。刻意不提供 set_persona，
    # 否则陪伴 agent 能伪造 actor='human' 改写自己的人格（后台 / CLI 才可写）。
    def persona(self):
        return persona_card.get(self.conn)

    def persona_block(self):
        return persona_card.render(self.conn)

    def tick(self, now=None):
        """离线维护：插件定时任务（主动发起候选、反思整理等）。"""
        return self.cortex.tick(now or _now())

    def close(self):
        self.conn.close()
