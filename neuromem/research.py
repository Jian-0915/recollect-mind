"""带出处的答问链（ROADMAP F2 / F1-F4 串接）。

一句话的完整闭环：
    识别实体 → 查独立知识库 → 没有才联网 → 存回独立库 → 带出处生成回答

任何一环失败都优雅降级：
    - 网络挂了：用知识库旧条目；没有就明说「这次没查到」，绝不编。
    - 模型挂了：直接把来源列表原样返回，让用户自己看。
    - 没有可查实体：返回 need_search=False，交回正常陪伴对话链路。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config, intent, knowledge, tools
from .knowledge import KnowledgeHit
from .intent import ResearchIntent


@dataclass
class ResearchResult:
    answer: str = ""
    sources: list[KnowledgeHit] = field(default_factory=list)
    need_search: bool = False
    used_cache: bool = False
    searched: bool = False
    query: str = ""
    note: str = ""
    intent: ResearchIntent | None = None

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "sources": [{"title": s.title, "url": s.url, "snippet": s.snippet,
                         "fetched_at": s.fetched_at, "provider": s.provider}
                        for s in self.sources],
            "need_search": self.need_search,
            "used_cache": self.used_cache,
            "searched": self.searched,
            "query": self.query,
            "note": self.note,
        }


_ANSWER_PROMPT = """你是用户的记忆陪伴助手。用户提到了一个新东西，请基于下面的资料回答。

严格规则：
1. 只使用资料里有的事实。资料没提到的，说「资料里没查到」或「我还不清楚」。
2. 引用事实时在句末标出处编号，如 [1]、[2]。编号对应下面的资料。
3. 语气像朋友在分享刚查到的东西，自然、简短（120 字以内），不要书面报告腔。
4. 不要编造 URL、日期、数字。不要输出 markdown 标题。

资料：
{evidence}

用户说的：{text}
"""


def research(text: str, *, kb_conn=None, private_terms=(), provider=None,
             generate_fn=None, llm_fn=None, force: bool = False,
             max_results: int = 5) -> ResearchResult:
    """主入口。只读主库，写只写独立知识库。

    generate_fn(text_prompt) -> str 可注入，便于测试；不传则用 llm.generate。
    """
    own_conn = kb_conn is None
    conn = kb_conn or knowledge.connect()
    try:
        i = intent.build_intent(text, private_terms)
        if force and not i.need_search:
            # force 的场景（用户明确说「查一下」）：把整句当查询词兜底
            safe = tools.sanitize_query(text, private_terms)
            if safe:
                i = ResearchIntent(need_search=True, entity=safe[:12],
                                   query=safe, reason="用户明确要求查询",
                                   private_terms=list(private_terms))
        result = ResearchResult(need_search=i.need_search, query=i.query, intent=i)
        if not i.need_search:
            return result

        i = intent.refine_intent(i, text, llm=llm_fn)
        result.query = i.query

        # 第一步：先查自己的知识库（已收录就不联网）
        hits = knowledge.search(conn, i.entity, top_k=5)
        if hits:
            result.used_cache = True
            result.sources = hits
            result.note = "来自已收录的知识"
        else:
            # 第二步：联网，用脱敏后的查询词
            safe_query = tools.sanitize_query(i.query or i.entity, private_terms)
            if not safe_query:
                result.note = "查询词脱敏后为空，未联网"
                return result
            try:
                found = tools.search(safe_query, max_results=max_results,
                                     provider=provider)
            except Exception as exc:
                result.note = f"这次没查到（{type(exc).__name__}）"
                return result
            if not found:
                result.note = "这次没查到相关资料"
                return result
            added, _ = knowledge.add_results(conn, found, query=safe_query,
                                             embed_text=False)
            result.searched = True
            result.sources = knowledge.search(conn, i.entity, top_k=5)
            if not result.sources:
                # 存进去了但检索没命中：直接把 provider 结果当来源
                result.sources = [KnowledgeHit(0, r.title, r.url, r.snippet,
                                               0.0, r.fetched_at, "anysearch")
                                  for r in found[:max_results]]
            result.note = f"新查到 {added} 条"

        # 第三步：带出处生成回答（模型失败就退化为来源清单）
        if not result.sources:
            return result
        evidence = "\n\n".join(s.as_prompt(n) for n, s in enumerate(result.sources, 1))
        prompt = _ANSWER_PROMPT.format(evidence=evidence, text=text[:300])
        try:
            gen = generate_fn
            if gen is None:
                from . import llm
                gen = lambda p: llm.generate(
                    p, model=config.ANSWER_MODEL, max_tokens=400, timeout=60, think=False)
            answer = (gen(prompt) or "").strip()
            if answer:
                result.answer = answer
                return result
        except Exception:
            pass
        result.answer = "我查到这些，你看看：\n" + "\n".join(
            f"[{n}] {s.title} — {s.url}"
            for n, s in enumerate(result.sources[:3], 1))
        result.note += "（模型不可用，已退回来源清单）"
        return result
    finally:
        if own_conn and conn is not None:
            conn.close()


def private_terms_from_db(main_conn=None) -> list[str]:
    """从人设卡取称呼 / 宠物名，作为查询脱敏词表。只读，不写主库。"""
    terms: list[str] = []
    try:
        own = main_conn is None
        conn = main_conn
        if own:
            from . import db
            conn = db.connect()
        rows = conn.execute("SELECT name FROM personas WHERE name IS NOT NULL").fetchall()
        for r in rows:
            n = str(r[0]).strip()
            if n and len(n) <= 12:
                terms.append(n)
        try:
            from . import alias
            for r in conn.execute("SELECT alias FROM entity_alias").fetchall():
                n = str(r[0]).strip()
                if n and len(n) <= 12:
                    terms.append(n)
        except Exception:
            pass
        if own:
            conn.close()
    except Exception:
        pass
    return list(dict.fromkeys(terms))
