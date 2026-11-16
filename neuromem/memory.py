"""Memory SDK：给 agent 使用的记忆接口（进程内）。

用法：
    from neuromem import Memory
    m = Memory(user_name="小明")          # 对话者身份
    m.remember("我特别怕狗。")            # 写入（"我" -> 小明）
    m.remember("我答应过每周都来。", subject="self")  # v12：关于 agent 自己
    ans, items = m.ask("我害怕什么？")    # 检索 + 生成回答（"小明" -> 你）
    m.feedback(used_ids=["evt_x"])       # 回传本轮真正采用了哪几条记忆
    m.evolve()                            # 代谢

问答链路会自动写 adoption_log（L1 弱信号）；上层 agent 用 feedback() 给 L2 强信号。
"""
from . import answer as _answer
from . import db
from . import evolve as _evolve
from . import recall as _recall
from . import store as _store


class Memory:
    def __init__(self, path=None, user_name=None, default_subject=None):
        from . import config as _config
        self.conn = db.connect(path)
        self.user_name = user_name or _config.COUNTERPART_NAME
        self.default_subject = default_subject

    # ---------- L0 固定身份层（人工版本化，运行时只读） ----------
    def identity(self):
        """当前固定身份（含 version / checksum / profile）。"""
        from . import identity as _identity
        return _identity.get(self.conn)

    def identity_block(self):
        """每轮注入用的紧凑宪法文本。"""
        from . import identity as _identity
        return _identity.block(self.conn)

    def set_identity(self, changes, *, actor="human", reason=""):
        """人工升级固定身份；LLM / 自进化等 actor 会被拒绝。"""
        from . import identity as _identity
        return _identity.set_profile(self.conn, changes, actor=actor, reason=reason)

    def identity_history(self, limit=10):
        from . import identity as _identity
        return _identity.history(self.conn, limit=limit)

    # ---------- 写入 ----------
    def remember(self, text, subject=None):
        """写入记忆；把第一人称"我"解析为当前 user_name。

        v12：``subject`` 标注记忆属于谁（self / counterpart / shared / third_party），
        与「这话谁说的」正交。不传则用 __init__ 的 default_subject，再回落到默认值。
        """
        return _store.remember(self.conn, text, speaker=self.user_name,
                               subject=subject if subject is not None
                               else self.default_subject)

    # ---------- 检索（纯只读，不写库） ----------
    def search(self, query, top_k=None):
        return _recall.recall(self.conn, query, top_k=top_k)

    # ---------- 检索 + 回答 ----------
    def ask(self, query):
        items = self.search(query)
        trace = {}
        ans = _answer.answer(query, items, user_name=self.user_name, trace=trace)
        self._auto(query, items, ans, trace)
        return ans, items

    def _auto(self, text, items, summary, trace):
        """问答链路的 L1 自动采纳记录；日志失败不影响回答。"""
        from . import adoption as _adoption
        return _adoption.auto_record(self.conn, query=text, items=items,
                                     summary=summary, trace=trace)

    # ---------- 统一查询：结构化事件 + 总结 ----------
    def query(self, text, top_k=10, with_summary=True):
        from .query import query as _query
        return _query(self.conn, text, top_k=top_k,
                      with_summary=with_summary, user_name=self.user_name)

    # ---------- 代谢 ----------
    def evolve(self):
        return _evolve.tick(self.conn)

    # ---------- 处理一轮对话 ----------
    def observe(self, user_text, remember_it=True):
        # 先记住用户刚说的，再检索回答（这样当轮就能用上、不会"刚说完就不记得"）
        if remember_it and not user_text.rstrip().endswith(("?", "？")):
            try:
                self.remember(user_text)
            except Exception:
                pass
        items = self.search(user_text)
        trace = {}
        ans = _answer.answer(user_text, items, user_name=self.user_name, trace=trace)
        self._auto(user_text, items, ans, trace)
        return ans, items

    # ---------- 采纳反馈（L2 强信号） ----------
    def feedback(self, used_ids=None, rejected_ids=None, *, query_text=None,
                 context_text=None, outcome=None):
        """agent 回传：本轮真正采用了 / 拒绝了哪几条记忆。

        used_ids 按收敛式增益强化；rejected_ids 轻度降权（有下限）。
        context_text 是本轮用到的说法/回答复述，会触发**再巩固**：
        旧版本存档后，按这个上下文小步重编码；不传则用 query_text 净化后的实词。
        未列出的候选不处理 —— 被截断的好记忆不该被惩罚。
        """
        from . import adoption as _adoption
        rid = _adoption.record(self.conn, query=query_text, source="agent",
                               used_ids=used_ids, rejected_ids=rejected_ids,
                               outcome=outcome, weight=1.0)
        res = _adoption.apply(self.conn, used_ids=used_ids,
                              rejected_ids=rejected_ids, weak=False,
                              context_text=context_text, query=query_text)
        return {"log_id": rid, **res}

    def versions(self, event_id, limit=10):
        """某条记忆的历史版本（再巩固链，可回放）。"""
        from . import reconsolidation as _rc
        return _rc.versions(self.conn, event_id, limit=limit)

    def revert(self, event_id, revision=None):
        """把一条记忆回滚到历史版本。"""
        from . import reconsolidation as _rc
        return _rc.revert(self.conn, event_id, revision=revision)

    def adoptions(self, limit=10):
        """最近几轮采纳记录（可回放，也能攒成评测集）。"""
        from . import adoption as _adoption
        return _adoption.recent(self.conn, limit=limit)

    def stats(self):
        out = {}
        for t in ["events", "fragments", "bindings", "personas", "edges",
                  "concepts", "concept_links", "growth_log"]:
            out[t] = self.conn.execute("SELECT COUNT(*) c FROM %s" % t).fetchone()["c"]
        return out

    def close(self):
        self.conn.close()