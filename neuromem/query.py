"""统一查询接口：一次返回 结构化事件列表 + 模型总结 + 降级提示。"""
import re

from . import answer as _answer
from . import config
from . import persona as _persona
from . import persona_card as _persona_card
from . import recall as _recall

_Q_NOISE = re.compile(r"请问|帮我|告诉我|我想知道|你记得|还记得|一下|吗|呢|吧|[?？!！。，,、；;]")
_Q_FILLER = re.compile(r"什么|哪些|怎么样|如何|是什么|叫什么|名字|最近|都有")


def _slim(text):
    """去掉问句壳，留下实词：'用户养的猫叫什么名字' → '用户养的猫'（B2 重试用）。"""
    t = _Q_NOISE.sub("", text or "")
    t = _Q_FILLER.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _relation_context(conn, text):
    """问题里点名的人物 → 只取与他们相关、且仍有效的关系。"""
    try:
        pids = set(_recall._query_personas(conn, text))
        if not pids:
            return []
        return [r for r in _persona.active_relations(conn)
                if r["a_id"] in pids or r["b_id"] in pids]
    except Exception:
        return []


def query(conn, text, top_k=10, with_summary=True, user_name=None, tag=None):
    """检索并返回 {query, events, relations, summary, warnings}。

    v14：额外带 ``persona_block``（L0 人设短版），让 MCP / HTTP 问答通道
    与陪伴 agent 主通道拿到同一份人设；未设置人设时为空字符串。
    """
    info = {}
    try:
        persona_block = _persona_card.brief(conn)
    except Exception:
        persona_block = ""
    items = _recall.recall(conn, text, top_k=top_k, info=info, tag=tag)
    # 空结果时自动改写重试（A2/B2）：问句壳去掉后常能命中
    if not items:
        alt = _slim(text)
        if alt and alt != text:
            items = _recall.recall(conn, alt, top_k=top_k, info=info, tag=tag)
    rels = _relation_context(conn, text)
    goals = []
    try:
        from . import goals as _goals
        goals = _goals.active(conn, limit=5)
    except Exception:
        goals = []
    st = None
    if config.COMPANION_STATE:
        try:
            from . import companion as _companion
            st = _companion.state(conn)
        except Exception as e:
            info["state_error"] = str(e)[:200]
    summary = None
    trace = {}
    if with_summary and items:
        try:
            summary = _answer.answer(text, items, user_name=user_name,
                                     relations=rels, goals=goals, state=st,
                                     trace=trace, persona_block=persona_block)
        except Exception as e:  # 回答模型不可用：仍返回检索结果
            info["answer_error"] = str(e)[:300]
        # L1 自动采纳记录：只有真正进提示词、且回答不是拒答的依据才被弱强化
        try:
            from . import adoption as _adoption
            adopt = _adoption.auto_record(conn, query=text, items=items,
                                          summary=summary, trace=trace)
            info["adoption"] = adopt
            if adopt.get("error"):
                info["adoption_error"] = adopt["error"]
        except Exception as e:
            info["adoption_error"] = str(e)[:300]
    warnings = []
    if info.get("embed_error"):
        warnings.append("嵌入服务不可用，已降级为关键词/实体检索：" + info["embed_error"])
    if info.get("answer_error"):
        warnings.append("回答模型不可用：" + info["answer_error"])
    if info.get("adoption_error"):
        warnings.append("采纳日志写入失败：" + info["adoption_error"])
    return {"query": text, "events": items, "relations": rels,
            "goals": goals, "state": st, "summary": summary, "warnings": warnings,
            "adopted_ids": trace.get("adopted_ids", []),
            "persona_block": persona_block,
            "degraded": bool(info.get("embed_error"))}