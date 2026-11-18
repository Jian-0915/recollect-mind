"""NeuroMem API：只读查询 + 写操作（新增/编辑/删除/合并/对话）。"""
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (account, alias, auth, autoevolve, config, db, embed, evaluation,
               identity, knowledge, live, llm, meta, persona_card, proactive,
               rebuild, research, selfnarrative, settings, store, tagging,
               tools, usage, working_memory)
from . import query as querymod
from . import recall as recallmod
from .persona import (build_profile, heal_relations, list_personas,
                      list_relations, merge as merge_personas, normalize_type)
from . import persona

app = FastAPI(title="NeuroMem Viewer")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@app.middleware("http")
async def _auth_middleware(request, call_next):
    """存在 data/access_token.txt 时才启用；首页与静态资源放行。"""
    if not auth.allowed(request):
        return JSONResponse({"ok": False, "error": "未登录", "auth": True}, status_code=401)
    return await call_next(request)


@app.on_event("startup")
def _startup():
    autoevolve.start()
    proactive.start()


def conn():
    return db.connect()


# ---------------- 请求模型 ----------------
class EventIn(BaseModel):
    text: str
    speaker: str = config.COUNTERPART_NAME
    tags: List[str] = []
    subject: Optional[str] = None   # v12：self / counterpart / shared / third_party


class EventEdit(BaseModel):
    gist: Optional[str] = None
    place: Optional[str] = None
    time_text: Optional[str] = None
    occurred_at: Optional[str] = None
    affect: Optional[Dict[str, Any]] = None


class MergeIn(BaseModel):
    keep: str
    drop: str


class RelationIn(BaseModel):
    a: str
    b: str
    type: str
    strength: Optional[float] = 0.5
    since: Optional[str] = ""
    until: Optional[str] = ""
    evidence: Optional[str] = "manual"


class RelationEdit(BaseModel):
    type: Optional[str] = None
    strength: Optional[float] = None
    since: Optional[str] = None
    until: Optional[str] = None
    evidence: Optional[str] = None


class AskIn(BaseModel):
    query: str
    user_name: str = config.COUNTERPART_NAME


# ---------------- 只读 ----------------
@app.get("/api/overview")
def overview():
    c = conn()
    s = {t: c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
         for t in ["events", "fragments", "bindings", "personas", "edges",
                   "relations", "concepts", "concept_links", "growth_log"]}
    s["active_events"] = c.execute("SELECT COUNT(*) FROM events WHERE archived=0").fetchone()[0]
    s["archived_events"] = c.execute("SELECT COUNT(*) FROM events WHERE archived=1").fetchone()[0]
    mr = meta.report(c)
    s["stage"] = mr["stage"]
    s["level"] = mr["level"]
    s["unlocked"] = mr["unlocked"]
    s["evaluation"] = evaluation.evaluate(c)
    try:
        row = c.execute("SELECT valence,arousal,label,updated_at FROM emotion_state WHERE id=1").fetchone()
        s["emotion"] = dict(row) if row else None
    except Exception:
        s["emotion"] = None
    c.close()
    return s


@app.get("/api/events")
def events(q: str = "", archived: int = -1, persona: str = "", tag: str = "",
           limit: int = 300):
    where, params = [], []
    if q.strip():
        like = "%" + q.strip() + "%"
        where.append("(e.gist LIKE ? OR e.place LIKE ?)")
        params += [like, like]
    if archived in (0, 1):
        where.append("e.archived = ?")
        params.append(archived)
    if persona.strip():
        where.append("e.id IN (SELECT ed.source FROM edges ed JOIN personas p ON ed.target=p.id "
                     "WHERE ed.type='involves' AND p.canonical_name = ?)")
        params.append(persona.strip())
    if tag.strip():
        where.append("e.id IN (SELECT event_id FROM event_tags WHERE tag = ?)")
        params.append(tag.strip())
    sql = ("SELECT e.id,e.gist,e.place,e.time_text,e.occurred_at,e.affect_json,e.salience,"
           "e.activation,e.archived,e.consolidation_stage,e.created_at FROM events e")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY e.created_at DESC LIMIT ?"
    params.append(limit)

    c = conn()
    rows = c.execute(sql, params).fetchall()
    tags_map = tagging.by_events(c, [r["id"] for r in rows])
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        d["affect"] = json.loads(d.pop("affect_json") or "{}")
        d["tags"] = tags_map.get(r["id"], [])
        out.append(d)
    return out


@app.get("/api/tags")
def tags_list():
    c = conn()
    try:
        return tagging.all_tags(c)
    finally:
        c.close()


@app.get("/api/aliases")
def aliases_list():
    c = conn()
    try:
        return alias.all_aliases(c)
    finally:
        c.close()


@app.post("/api/aliases")
def aliases_add(body: Dict[str, Any]):
    a = (body.get("alias") or "").strip()
    t = (body.get("target") or "").strip()
    if not a or not t:
        return {"ok": False, "error": "需要 alias 与 target"}
    c = conn()
    try:
        created = alias.add(c, a, t, body.get("kind") or "category")
    finally:
        c.close()
    return {"ok": True, "alias": a, "target": t,
            "created": created, "note": "已关联" if created else "已存在，未重复"}


@app.delete("/api/aliases")
def aliases_delete(alias_name: str = "", target: str = ""):
    if not alias_name or not target:
        return {"ok": False, "error": "需要 alias_name 与 target"}
    c = conn()
    try:
        removed = alias.remove(c, alias_name, target)
    finally:
        c.close()
    return {"ok": True, "removed": removed}


@app.get("/api/events/{eid}")
def event_detail(eid: str):
    c = conn()
    try:
        row = c.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        if not row:
            return {"ok": False, "error": "not found"}
        d = dict(row)
        d.pop("embedding", None)
        d["affect"] = json.loads(d.pop("affect_json") or "{}")
        d["tags"] = tagging.for_event(c, eid)
        d["fragments"] = [
            dict(r) for r in c.execute(
                "SELECT f.id,f.text,f.modality FROM bindings b "
                "JOIN fragments f ON b.fragment_id=f.id WHERE b.event_id=? ORDER BY b.seq",
                (eid,))
        ]
        d["personas"] = [
            r["canonical_name"] for r in c.execute(
                "SELECT p.canonical_name FROM edges e JOIN personas p ON e.target=p.id "
                "WHERE e.source=? AND e.type='involves'", (eid,))
        ]
        d["concepts"] = [
            dict(r) for r in c.execute(
                "SELECT c.id,c.text,c.abstraction_level FROM concept_links cl "
                "JOIN concepts c ON cl.concept_id=c.id WHERE cl.event_id=?", (eid,))
        ]
        seen, neigh = set(), []
        for r in c.execute(
                "SELECT x.id,x.gist,e.weight FROM edges e JOIN events x "
                "ON (x.id=e.source OR x.id=e.target) "
                "WHERE (e.source=? OR e.target=?) AND e.type='semantic' AND x.id<>? "
                "LIMIT 16", (eid, eid, eid)):
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            neigh.append(dict(r))
        d["neighbors"] = neigh[:8]
        d["working"] = bool(eid in set(working_memory.recent_ids(
            c, hours=config.WM_HOURS, limit=config.WM_CAPACITY)))
        d["ok"] = True
        return d
    finally:
        c.close()


@app.get("/api/concepts/{cid}")
def concept_detail(cid: str):
    c = conn()
    try:
        row = c.execute(
            "SELECT id,text,abstraction_level,confidence,created_at "
            "FROM concepts WHERE id=?", (cid,)).fetchone()
        if not row:
            return {"ok": False, "error": "not found"}
        d = dict(row)
        d["events"] = [dict(r) for r in c.execute(
            "SELECT e.id,e.gist,e.occurred_at,e.time_text,e.place,cl.type "
            "FROM concept_links cl JOIN events e ON cl.event_id=e.id "
            "WHERE cl.concept_id=? ORDER BY e.created_at", (cid,))]
        d["ok"] = True
        return d
    finally:
        c.close()


@app.delete("/api/concepts/{cid}")
def delete_concept(cid: str):
    c = conn()
    try:
        row = c.execute("SELECT id FROM concepts WHERE id=?", (cid,)).fetchone()
        if not row:
            return {"ok": False, "error": "概念不存在"}
        c.execute("DELETE FROM concept_links WHERE concept_id=?", (cid,))
        c.execute("DELETE FROM concepts WHERE id=?", (cid,))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


class ConceptMergeIn(BaseModel):
    keep: str
    drop: str


@app.post("/api/concepts/merge")
def merge_concepts(body: ConceptMergeIn):
    """把 drop 概念的来源事件并入 keep，然后删除 drop（人工治理脏概念）。"""
    if not body.keep or not body.drop or body.keep == body.drop:
        return {"ok": False, "error": "keep 与 drop 必须是两个不同概念"}
    c = conn()
    try:
        ids = {r["id"] for r in c.execute(
            "SELECT id FROM concepts WHERE id IN (?,?)", (body.keep, body.drop))}
        if len(ids) < 2:
            return {"ok": False, "error": "概念不存在"}
        c.execute(
            "INSERT INTO concept_links(concept_id,event_id,type) "
            "SELECT ?,event_id,type FROM concept_links WHERE concept_id=? "
            "AND event_id NOT IN (SELECT event_id FROM concept_links WHERE concept_id=?)",
            (body.keep, body.drop, body.keep))
        c.execute("DELETE FROM concept_links WHERE concept_id=?", (body.drop,))
        c.execute("DELETE FROM concepts WHERE id=?", (body.drop,))
        c.commit()
        n = c.execute("SELECT COUNT(*) FROM concept_links WHERE concept_id=?",
                      (body.keep,)).fetchone()[0]
        return {"ok": True, "keep": body.keep, "events": n}
    finally:
        c.close()


@app.post("/api/self/rebuild")
def self_rebuild():
    c = conn()
    try:
        try:
            text = selfnarrative.build(c, None)
        except Exception as e:
            return {"ok": False, "error": "生成失败：%s" % str(e)[:200]}
    finally:
        c.close()
    return {"ok": bool(text), "text": text or ""}


@app.get("/api/self")
def self_info():
    c = conn()
    try:
        row = selfnarrative.get(c)
    finally:
        c.close()
    return {"text": (row or {}).get("text"), "updated_at": (row or {}).get("updated_at")}


@app.get("/api/concepts")
def concepts():
    c = conn()
    rows = c.execute(
        "SELECT c.id,c.text,c.abstraction_level,c.confidence,COUNT(cl.event_id) n "
        "FROM concepts c LEFT JOIN concept_links cl ON c.id=cl.concept_id "
        "GROUP BY c.id ORDER BY c.abstraction_level, c.created_at DESC").fetchall()
    c.close()
    return [dict(r) for r in rows]


@app.get("/api/personas")
def personas():
    c = conn()
    out = list_personas(c)
    c.close()
    return out


@app.get("/api/personas/{pid}")
def persona_detail(pid: str):
    c = conn()
    try:
        row = c.execute(
            "SELECT id,canonical_name,aliases_json,profile,profile_at,profile_events FROM personas WHERE id=?",
            (pid,)).fetchone()
        if not row:
            return {"ok": False, "error": "not found"}
        d = dict(row)
        d["aliases"] = json.loads(d.pop("aliases_json") or "[]")
        events = []
        for r in c.execute(
                "SELECT e.id,e.gist,e.occurred_at,e.time_text,e.place,e.affect_json "
                "FROM edges ed JOIN events e ON ed.source=e.id "
                "WHERE ed.target=? AND ed.type='involves' "
                "ORDER BY COALESCE(e.occurred_at, e.created_at) DESC LIMIT 50", (pid,)):
            e = dict(r)
            e["affect"] = json.loads(e.pop("affect_json") or "{}")
            events.append(e)
        d["events"] = events
        tmap = tagging.by_events(c, [e["id"] for e in events])
        for e in events:
            e["tags"] = tmap.get(e["id"], [])
        names = {r["id"]: r["canonical_name"] for r in c.execute("SELECT id,canonical_name FROM personas")}
        rels = []
        for r in c.execute(
                "SELECT rel_type,strength,"
                "CASE WHEN a=? THEN b ELSE a END other_id FROM relations WHERE a=? OR b=?",
                (pid, pid, pid)):
            other = names.get(r["other_id"])
            rels.append({"type": r["rel_type"], "strength": r["strength"],
                         "other": other or "", "other_id": r["other_id"],
                         "other_missing": other is None})
        d["relations"] = rels
        d["ok"] = True
        return d
    finally:
        c.close()


@app.post("/api/personas/{pid}/profile")
def persona_profile(pid: str):
    c = conn()
    try:
        row = c.execute("SELECT canonical_name FROM personas WHERE id=?", (pid,)).fetchone()
        if not row:
            return {"ok": False, "error": "not found"}
        try:
            text = build_profile(c, row["canonical_name"])
        except Exception as e:
            return {"ok": False, "error": "画像生成失败：%s" % str(e)[:200]}
        return {"ok": bool(text), "profile": text or ""}
    finally:
        c.close()


@app.put("/api/personas/{pid}/aliases")
def persona_aliases(pid: str, body: Dict[str, Any]):
    names = [str(x).strip() for x in (body.get("aliases") or []) if str(x).strip()]
    c = conn()
    try:
        if not c.execute("SELECT 1 FROM personas WHERE id=?", (pid,)).fetchone():
            return {"ok": False, "error": "not found"}
        c.execute("UPDATE personas SET aliases_json=? WHERE id=?",
                  (json.dumps(names, ensure_ascii=False), pid))
        c.commit()
    finally:
        c.close()
    return {"ok": True, "aliases": names}


@app.delete("/api/personas/{pid}")
def delete_persona(pid: str, force: int = 0):
    c = conn()
    try:
        row = c.execute("SELECT canonical_name FROM personas WHERE id=?", (pid,)).fetchone()
        if not row:
            return {"ok": False, "error": "人物不存在"}
        n = c.execute(
            "SELECT COUNT(*) FROM edges WHERE target=? AND type='involves'", (pid,)).fetchone()[0]
        if n and not force:
            return {"ok": False, "need_force": True,
                    "error": "该人物还有 %d 条关联记忆；确认要解除绑定并删除？" % n}
        c.execute("DELETE FROM edges WHERE (source=? OR target=?) AND type='involves'", (pid, pid))
        c.execute("DELETE FROM relations WHERE a=? OR b=?", (pid, pid))
        c.execute("DELETE FROM personas WHERE id=?", (pid,))
        c.commit()
        return {"ok": True, "unbound": n}
    finally:
        c.close()


@app.get("/api/relations")
def relations():
    c = conn()
    out = list_relations(c)
    c.close()
    return out


@app.post("/api/relations")
def add_relation(r: RelationIn):
    a, b, t = r.a.strip(), r.b.strip(), r.type.strip()
    t = persona.normalize_type(t)
    if not (a and b and t):
        return {"ok": False, "error": "人物与关系类型都不能为空"}
    if a == b:
        return {"ok": False, "error": "不能和自己建立关系"}
    c = conn()
    try:
        from .persona import ensure
        pa, pb = ensure(c, a), ensure(c, b)
        if not pa or not pb:
            return {"ok": False, "error": "人物创建失败"}
        dup = c.execute(
            "SELECT id FROM relations WHERE rel_type=? AND ((a=? AND b=?) OR (a=? AND b=?))",
            (t, pa["id"], pb["id"], pb["id"], pa["id"])).fetchone()
        if dup:
            c.execute("UPDATE relations SET strength=? WHERE id=?", (r.strength, dup["id"]))
            c.commit()
            return {"ok": True, "id": dup["id"], "updated": True}
        rid = "rel_" + uuid.uuid4().hex[:12]
        c.execute(
            "INSERT INTO relations(id,a,b,rel_type,strength,since,until,evidence) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (rid, pa["id"], pb["id"], t, r.strength, r.since or "", r.until or "",
             r.evidence or "manual"))
        c.commit()
        return {"ok": True, "id": rid, "updated": False}
    finally:
        c.close()


@app.put("/api/relations/{rid}")
def edit_relation(rid: str, e: RelationEdit):
    sets, vals = [], []
    if e.type is not None:
        if not e.type.strip():
            return {"ok": False, "error": "关系类型不能为空"}
        sets.append("rel_type=?"); vals.append(e.type.strip())
    if e.strength is not None:
        sets.append("strength=?"); vals.append(max(0.0, min(1.0, float(e.strength))))
    if e.since is not None:
        sets.append("since=?"); vals.append(e.since)
    if e.until is not None:
        sets.append("until=?"); vals.append(e.until)
    if e.evidence is not None:
        sets.append("evidence=?"); vals.append(e.evidence)
    if not sets:
        return {"ok": True, "updated": 0}
    c = conn()
    try:
        vals.append(rid)
        cur = c.execute("UPDATE relations SET " + ", ".join(sets) + " WHERE id=?", vals)
        c.commit()
        return {"ok": True, "updated": cur.rowcount}
    finally:
        c.close()


@app.delete("/api/relations/{rid}")
def delete_relation(rid: str):
    c = conn()
    try:
        cur = c.execute("DELETE FROM relations WHERE id=?", (rid,))
        c.commit()
        return {"ok": cur.rowcount > 0}
    finally:
        c.close()


@app.post("/api/relations/heal")
def heal_relations_ep():
    """修复一端指向已删除人物的关系（按关系词找回同名人物卡）。

    关系页上那些显示成 p_xxxx 的行就来自这类悬空数据；能修的按证据修，
    修不了的返回 remaining 让前端继续标「人物已删除」，不猜、不删。
    """
    c = conn()
    try:
        actions = heal_relations(c, dry_run=False)
        remaining = [r for r in list_relations(c) if r["broken"]]
        return {"ok": True, "healed": [
            {"id": rid, "rel_type": t, "from": old, "to": new, "to_name": name}
            for (rid, t, old, new, name) in actions],
            "remaining": [{"id": r["id"], "type": r["type"],
                          "a": r["a"] or r["a_id"], "b": r["b"] or r["b_id"]}
                         for r in remaining]}
    finally:
        c.close()


@app.get("/api/graph")
def graph():
    c = conn()
    nodes, ids = [], set()
    for r in c.execute("SELECT id,gist FROM events WHERE archived=0 LIMIT 300"):
        nodes.append({"id": r["id"], "name": (r["gist"] or "")[:16], "category": "event"})
        ids.add(r["id"])
    for r in c.execute("SELECT id,canonical_name,kind,bond FROM personas"):
        # category 仍是 persona（前端按它上色），kind/bond 让前端能把宠物、
        # 「只被提及的人」（刘慈欣/周杰伦）显式区分开，而不是混在社交图里
        nodes.append({"id": r["id"], "name": r["canonical_name"],
                      "category": "persona",
                      "kind": r["kind"] or "person",
                      "bond": r["bond"] or "social"})
        ids.add(r["id"])
    for r in c.execute("SELECT id,text FROM concepts"):
        nodes.append({"id": r["id"], "name": (r["text"] or "")[:16], "category": "concept"})
        ids.add(r["id"])
    links = []
    for r in c.execute("SELECT source,target,type FROM edges"):
        if r["source"] in ids and r["target"] in ids:
            links.append({"source": r["source"], "target": r["target"], "name": r["type"]})
    for r in c.execute("SELECT a,b,rel_type FROM relations"):
        if r["a"] in ids and r["b"] in ids:
            links.append({"source": r["a"], "target": r["b"], "name": r["rel_type"]})
    c.close()
    return {"nodes": nodes[:400], "links": links[:800]}


@app.get("/api/growth")
def growth():
    c = conn()
    rows = c.execute("SELECT * FROM growth_log ORDER BY id").fetchall()
    c.close()
    return [dict(r) for r in rows]


@app.get("/api/meta")
def meta_info():
    c = conn()
    out = meta.report(c)
    c.close()
    return out


# ---------------- 写操作 ----------------
@app.post("/api/events")
def add_event(e: EventIn):
    c = conn()
    try:
        ids = store.remember(c, e.text, speaker=e.speaker, tags=e.tags,
                             subject=e.subject)
    except Exception as ex:  # 抽取模型不可用时给出可读错误，不抛 500
        return {"ids": [], "error": "写入失败：%s" % str(ex)[:300]}
    finally:
        c.close()
    autoevolve.maybe_trigger()   # 增量达阈值则异步补一轮代谢
    n, dup = store.LAST_WRITE.get("new", 0), store.LAST_WRITE.get("dup", 0)
    if not ids:
        return {"ids": [], "message": "内容里没有可记忆的事件（寒暄或纯符号），未写入。"}
    if dup and not n:
        return {"ids": ids, "message": "已存在，未重复写入"}
    if n and dup:
        return {"ids": ids, "message": "新增 %d 条，另有 %d 条已存在" % (n, dup)}
    return {"ids": ids, "message": "已记住 %d 条" % n}


@app.put("/api/events/{eid}")
def edit_event(eid: str, e: EventEdit):
    c = conn()
    sets, vals, warnings = [], [], []
    if e.gist is not None:
        sets.append("gist=?"); vals.append(e.gist)
        # 内容变了必须重算向量，否则检索仍在用旧语义
        try:
            vec = embed.embed(e.gist)
            sets.append("embedding=?"); vals.append(db.pack(vec))
            # 旧语义边基于旧内容，先移除，避免把错误关联扩散进来
            c.execute("DELETE FROM edges WHERE (source=? OR target=?) AND type='semantic'",
                      (eid, eid))
            warnings.append("旧语义边已移除（如需重建可重新写入该记忆）")
        except Exception as ex:
            warnings.append("向量重算失败，检索仍使用旧向量：%s" % str(ex)[:120])
    if e.place is not None:
        sets.append("place=?"); vals.append(e.place)
    if e.time_text is not None:
        sets.append("time_text=?"); vals.append(e.time_text)
    if e.occurred_at is not None:
        sets.append("occurred_at=?"); vals.append(e.occurred_at)
    if e.affect is not None:
        sets.append("affect_json=?"); vals.append(json.dumps(e.affect, ensure_ascii=False))
    if sets:
        vals.append(eid)
        c.execute("UPDATE events SET " + ", ".join(sets) + " WHERE id=?", vals)
        c.commit()
    c.close()
    return {"ok": True, "warnings": warnings}


@app.delete("/api/events/{eid}")
def delete_event(eid: str):
    c = conn()
    c.execute("DELETE FROM events WHERE id=?", (eid,))
    c.execute("DELETE FROM bindings WHERE event_id=?", (eid,))
    c.execute("DELETE FROM edges WHERE source=? OR target=?", (eid, eid))
    c.execute("DELETE FROM concept_links WHERE event_id=?", (eid,))
    c.commit()
    c.close()
    return {"ok": True}


@app.post("/api/events/{eid}/archive")
def archive_event(eid: str, archived: int = 1):
    c = conn()
    if archived:
        c.execute("UPDATE events SET archived=1 WHERE id=?", (eid,))
    else:
        # 恢复视为"重新想起"：激活值拉回 1.0，否则下一轮又被归档
        c.execute("UPDATE events SET archived=0, activation=1.0 WHERE id=?", (eid,))
    c.commit()
    c.close()
    return {"ok": True}


@app.post("/api/personas/merge")
def merge_personas_api(m: MergeIn):
    c = conn()
    try:
        ok = merge_personas(c, m.keep, m.drop)
    finally:
        c.close()
    return {"ok": ok}


# ---------------- 对话 ----------------
@app.post("/api/ask")
def ask_api(a: AskIn):
    c = conn()
    try:
        r = querymod.query(c, a.query, user_name=a.user_name)
    except Exception as e:  # 兜底：不把 500 抛给前端
        return {"query": a.query, "events": [], "summary": None,
                "warnings": ["检索失败：%s" % str(e)[:300]]}
    finally:
        c.close()
    return r


# ---------------- 外部知识（独立库，不碰 events/concepts） ----------------
class ResearchIn(BaseModel):
    text: str
    force: bool = False


@app.post("/api/research")
def research_api(a: ResearchIn):
    """带出处的答问：识别实体 → 查独立知识库 → 必要时联网 → 带来源回答。

    同步执行（联网 + 生成约十几秒）；桌面端桥接层需在后台线程调用，
    避免阻塞 WebView UI。任何失败在 research.research 内部已降级为
    「没查到」，不会把 500 抛给前端。
    """
    try:
        main_c = conn()
        try:
            private = research.private_terms_from_db(main_c)
        finally:
            main_c.close()
        r = research.research(a.text or "", private_terms=private, force=a.force)
        return {"ok": True, **r.to_dict()}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "answer": "", "sources": []}


@app.get("/api/knowledge")
def knowledge_list(limit: int = 30):
    """「她知道了什么」面板：按抓取时间倒序列出独立知识库条目。"""
    c = None
    try:
        c = knowledge.connect()
        return {"ok": True, "items": [
            {"title": h.title, "url": h.url, "snippet": h.snippet,
             "fetched_at": h.fetched_at, "provider": h.provider}
            for h in knowledge.list_recent(c, min(max(limit, 1), 200))],
            **knowledge.stats(c)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "items": []}
    finally:
        if c is not None:
            c.close()


@app.get("/api/knowledge/search")
def knowledge_search(q: str = "", limit: int = 8):
    c = None
    try:
        c = knowledge.connect()
        hits = knowledge.search(c, q, top_k=min(max(limit, 1), 30))
        return {"ok": True, "query": q, "items": [
            {"title": h.title, "url": h.url, "snippet": h.snippet,
             "fetched_at": h.fetched_at, "score": h.score} for h in hits]}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "items": []}
    finally:
        if c is not None:
            c.close()


# ---------------- 模型配置 ----------------
def _dim_warnings(saved):
    warns = []
    c = conn()
    try:
        old = embed.stored_dim(c)
    finally:
        c.close()
    new = (saved.get("embed") or {}).get("dim")
    if old and new and int(old) != int(new):
        warns.append("库内已有向量维度 %s，当前嵌入模型维度 %s，两者不可比；"
                     "切换后旧记忆将检索不到，建议清空记忆重建。" % (old, new))
    return warns


# ---------------- 全库向量重建（换嵌入模型后重算已有记忆） ----------------
@app.post("/api/embed/rebuild")
def start_rebuild():
    ok, msg = rebuild.start()
    return {"ok": ok, "message": msg, "state": rebuild.state()}


@app.get("/api/embed/rebuild/status")
def rebuild_status():
    return rebuild.state()


@app.post("/api/embed/rebuild/cancel")
def cancel_rebuild():
    ok, msg = rebuild.cancel()
    return {"ok": ok, "message": msg, "state": rebuild.state()}


@app.get("/api/settings")
def get_settings():
    return {"settings": settings.public(), "presets": settings.PRESETS}


@app.put("/api/settings")
def put_settings(cfg: Dict[str, Any]):
    cfg = cfg or {}
    if not any(k in cfg for k in ("llm", "embed", "clear_key")):
        return {"ok": False, "error": "请求体为空，未做任何修改"}

    fields = {
        "llm": {"provider", "base_url", "api_key", "extract_model", "answer_model",
                "timeout", "temperature", "think"},
        "embed": {"provider", "base_url", "api_key", "model", "dim", "timeout"},
    }
    cur = settings.current()
    body = {}
    for section in ("llm", "embed"):
        vals = dict(cfg.get(section) or {})
        vals = {k: v for k, v in vals.items() if k in fields[section]}
        provider = vals.get("provider")
        if provider is not None:
            if provider not in ("ollama", "openai_compat"):
                return {"ok": False, "error": "provider 只能是 ollama 或 openai_compat"}
            # 换 provider 必须同时给 base_url，防止空表单把云端配置覆盖成默认 ollama
            if (provider != (cur.get(section) or {}).get("provider")
                    and not str(vals.get("base_url") or "").strip()):
                return {"ok": False, "error": "切换 provider 时必须填写 base_url"}
        key = vals.get("api_key")
        # 留空 / 掩码回传 = 不修改，避免"以为换了 Key 其实没换"
        if key is None or (isinstance(key, str) and (not key.strip() or key.startswith("****"))):
            vals.pop("api_key", None)
        if vals:
            body[section] = vals
    ck = cfg.get("clear_key")
    if ck is True or ck == "llm":
        body.setdefault("llm", {})["api_key"] = ""
    if ck == "embed":
        body.setdefault("embed", {})["api_key"] = ""
    if not body:
        return {"ok": False, "error": "没有可保存的字段"}
    saved = settings.save(body)
    return {"ok": True, "settings": settings.public(saved), "warnings": _dim_warnings(saved)}


@app.post("/api/settings/test")
def test_settings(p: Optional[Dict[str, Any]] = None):
    p = p or {}
    role = p.get("role", "llm")
    t0 = time.time()
    try:
        if role == "embed":
            vec = embed.embed(p.get("text") or "连接测试")
            return {"ok": True, "latency": round(time.time() - t0, 2), "dim": len(vec)}
        txt = llm.generate(p.get("text") or "只回复OK", max_tokens=16, timeout=45)
        return {"ok": True, "latency": round(time.time() - t0, 2), "reply": (txt or "")[:80]}
    except Exception as e:
        msg = str(e)[:300]
        return {"ok": False, "latency": round(time.time() - t0, 2), "error": msg,
                "need_key": ("401" in msg or "Authorization" in msg)}


@app.get("/api/settings/models")
def settings_models(provider: str = "ollama", base_url: str = "", api_key: str = ""):
    import requests

    cur = settings.current()
    if not base_url:
        base_url = cur["llm"].get("base_url") or config.OLLAMA_URL
    if isinstance(api_key, str) and api_key.startswith("****"):
        api_key = cur["llm"].get("api_key", "")
    if not api_key and (base_url or "").rstrip("/") == (cur["llm"].get("base_url") or "").rstrip("/"):
        api_key = cur["llm"].get("api_key", "")  # 未填 = 沿用已保存的 Key
    try:
        if (provider or "ollama").lower() == "ollama":
            url = settings.ollama_base(base_url) + "/api/tags"
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            return {"ok": True, "models": [m.get("name") for m in r.json().get("models", [])]}
        url = settings.openai_base(base_url) + "/models"
        r = requests.get(url, headers=settings.openai_headers({"api_key": api_key}), timeout=20)
        if r.status_code in (401, 403):
            detail = ""
            try:
                err = (r.json() or {}).get("error")
                detail = (err.get("message") if isinstance(err, dict) else str(err or "")) or ""
            except Exception:
                detail = (r.text or "")[:120]
            return {"ok": False, "need_key": True,
                    "error": "HTTP %d 未授权：%s" % (r.status_code, detail or "该端点需要 API Key")}
        r.raise_for_status()
        return {"ok": True, "models": [m.get("id") for m in r.json().get("data", [])]}
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", 0)
        return {"ok": False, "error": "HTTP %s：%s" % (code, str(e)[:160])}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ---------------- L0 固定身份层（人工版本化，运行时只读） ----------------
class IdentityIn(BaseModel):
    confirm: str = ""
    actor: str = "human"
    reason: str = ""
    profile: Dict[str, str] = {}


@app.get("/api/identity")
def get_identity():
    """读固定身份 + checksum 校验 + 每轮可注入的紧凑文本。"""
    c = conn()
    try:
        cur = identity.get(c) or {}
        ok, msg = identity.verify(c)
        return {"ok": ok, "verify": msg, **cur, "block": identity.block(c)}
    finally:
        c.close()


@app.get("/api/identity/history")
def get_identity_history(limit: int = 10):
    c = conn()
    try:
        return {"ok": True, "items": identity.history(c, limit=limit)}
    finally:
        c.close()


@app.put("/api/identity")
def put_identity(body: IdentityIn):
    """人工升级固定身份：必须 confirm="IDENTITY"，actor 属人工白名单。

    刻意不提供「让 LLM 生成身份」入口 —— 旧系统把自传当身份，正是噪声来源。
    """
    if body.confirm != "IDENTITY":
        return {"ok": False, "error": '需要 confirm="IDENTITY"'}
    c = conn()
    try:
        return identity.set_profile(c, body.profile, actor=body.actor,
                                    reason=body.reason)
    finally:
        c.close()


# ---------------- L0 人设卡（后台管理的统一设置入口） ----------------
# 与宪法层一样：只能人工版本化升级。后台 / CLI 是唯一人工入口，
# agent 侧（Brain / MCP）永远只有只读视图。
class PersonaIn(BaseModel):
    confirm: str = ""
    actor: str = "human"
    reason: str = ""
    card: Dict[str, Any] = {}


class PersonaRollbackIn(BaseModel):
    confirm: str = ""
    version: int = 0
    actor: str = "human"
    reason: str = ""


@app.get("/api/persona")
def get_persona():
    """当前人设 + 校验 + 宪法 + 注入到 system 的文本，供后台表单回填。"""
    c = conn()
    try:
        cur = persona_card.get(c) or {}
        ok, msg = persona_card.verify(c)
        cur_identity = identity.get(c) or {}
        return {"ok": ok, "verify": msg, **cur,
                "render": persona_card.render(c),
                "brief": persona_card.brief(c),
                "identity": cur_identity.get("profile"),
                "identity_version": cur_identity.get("version"),
                "identity_verify": identity.verify(c)[1],
                "identity_block": identity.block(c),
                "call_user": persona_card.call_user(c)}
    finally:
        c.close()


@app.get("/api/persona/template")
def get_persona_template(example: bool = True):
    """后台「填入示例」按钮：example=false 给中性骨架。"""
    return {"ok": True, "template": persona_card.template(example=example)}


@app.post("/api/persona/preview")
def preview_persona(body: Dict[str, Any]):
    """只校验 + 渲染，绝不写库。后台边改边看 agent 实际会收到的文本。"""
    card = (body or {}).get("card")
    c = conn()
    try:
        return persona_card.preview(c, card)
    finally:
        c.close()


@app.put("/api/persona")
def put_persona(body: PersonaIn):
    """人工保存新版本：confirm="PERSONA"，actor 必须属于人工白名单。"""
    if body.confirm != "PERSONA":
        return {"ok": False, "error": '需要 confirm="PERSONA"'}
    c = conn()
    try:
        return persona_card.apply(c, body.card, actor=body.actor,
                                   reason=body.reason or "后台管理修改")
    finally:
        c.close()


@app.get("/api/persona/history")
def get_persona_history(limit: int = 10):
    c = conn()
    try:
        return {"ok": True, "items": persona_card.history(c, limit=limit),
                "verify": persona_card.verify(c)[1]}
    finally:
        c.close()


@app.post("/api/persona/rollback")
def rollback_persona(body: PersonaRollbackIn):
    """把历史版本恢复成新的当前版本（不删历史，版本继续递增）。"""
    if body.confirm != "PERSONA":
        return {"ok": False, "error": '需要 confirm="PERSONA"'}
    c = conn()
    try:
        return persona_card.rollback(c, body.version, actor=body.actor,
                                     reason=body.reason)
    finally:
        c.close()


# ---------------- v13 Brain：陪伴 agent 的统一入口 ----------------
# 调试 / 非 Python agent 也能走 HTTP；陪伴 agent 本体请用进程内
# ``from neuromem import Brain``，少一次序列化、也少一个生成主体。
class BrainTurnIn(BaseModel):
    text: str = ""
    top_k: int = 12
    session_id: str = "default"
    affect: Any = None


class BrainCommitIn(BaseModel):
    reply: str = ""
    used_ids: List[str] = []
    rejected_ids: List[str] = []
    query_text: str = ""
    context_text: str = ""
    outcome: str = "answered"
    affect: Any = None
    topic: str = ""
    open_topic: str = ""
    user_text: Optional[str] = None
    session_id: str = "default"


def _brain(session_id):
    from .brain import Brain
    return Brain(session_id=session_id or "default")


@app.post("/api/brain/turn")
def brain_turn(body: BrainTurnIn):
    """只给候选与上下文，不生成回答。返回结构与 ContextPack 一致。"""
    b = _brain(body.session_id)
    try:
        pack = b.turn(body.text, top_k=body.top_k, affect_now=body.affect)
        return dict(pack)
    finally:
        b.close()


@app.post("/api/brain/commit")
def brain_commit(body: BrainCommitIn):
    """L2 采纳 + 再巩固 + 会话轮次 + 多维情感 + 插件副作用一次回写。"""
    b = _brain(body.session_id)
    try:
        return b.commit(
            body.reply, used_ids=body.used_ids, rejected_ids=body.rejected_ids,
            query_text=body.query_text, context_text=body.context_text,
            outcome=body.outcome, affect_input=body.affect,
            topic=body.topic or None, open_topic=body.open_topic or None,
            user_text=body.user_text)
    finally:
        b.close()


@app.get("/api/affect")
def get_affect():
    """多维情感快照（离散情绪 / 慢心境 / 能量 / 按人关系情感）。"""
    from . import affect
    c = conn()
    try:
        return {"ok": True, "state": affect.snapshot(c),
                "hints": affect.expression_hints(affect.snapshot(c))}
    finally:
        c.close()


@app.get("/api/cortex")
def get_cortex():
    from . import cortex
    return {"ok": True, "available": cortex.available(),
            "enabled": list(config.CORTEX_MODULES),
            "enabled_flag": config.CORTEX_ENABLED}


@app.post("/api/cortex/tick")
def cortex_tick():
    b = _brain("tick")
    try:
        return {"ok": True, "result": b.tick()}
    finally:
        b.close()


@app.get("/api/proactive")
def proactive_peek():
    """主动发起出口：取一条未消费候选，包装成自然话语。展示即消费，不会重复弹。

    独立于 /api/ask：这里是 agent「想起你」，不是你问它答。无候选时 available=False，
    前端据此决定不打扰。生成失败有兜底短句，不会抛 500。
    """
    c = conn()
    try:
        return {"ok": True, **proactive.peek(c)}
    except Exception as e:
        return {"ok": False, "available": False,
                "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


@app.get("/api/proactive/status")
def proactive_status():
    return {"ok": True, **proactive.status()}


@app.post("/api/proactive/tick")
def proactive_tick():
    """手动触发一轮候选产出（供验证 / 调试；后台已有定时器）。"""
    c = conn()
    try:
        return {"ok": True, "result": proactive.tick(c)}
    finally:
        c.close()


# ---------------- 陪伴对话通道（真写记忆） ----------------
class LiveIn(BaseModel):
    query: str


@app.post("/api/live")
def live_turn(body: LiveIn):
    """陪伴对话：真走 CompanionAgent，会写会话轮次与事实。

    与 /api/ask 的分工：ask 只读召回（查记忆），live 才沉淀记忆。
    这是主库唯一受控写入入口：只写 L1/L2 与会话状态，绝不碰 L0。
    """
    try:
        return live.turn(body.query)
    except Exception as e:
        return {"ok": False,
                "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


@app.get("/api/live/status")
def live_status():
    return {"ok": True, **live.status()}


@app.post("/api/live/reset")
def live_reset():
    """丢弃常驻实例；后台改过人设后调一次，下次对话重新读 L0。"""
    return live.reset()


# ---------------- 数据管理：导出 / 清库 ----------------
# 清库时保留的「配置 / 账号」表。其余业务表一律从 sqlite_master 动态枚举：
# 以后再加机制表（版本链、采纳日志、会话、心境…）会自动纳入清库范围，
# 不用回来改手工清单 —— v11 之前就是这样漏掉了 event_tags / entity_alias。
# v12：identity / identity_history 属于 L0 固定身份层，是人工设定的「宪法」，
# 清库必须豁免（清库清的是经历，不是「我是谁」）。
RESET_KEEP = {"settings", "accounts", "sessions", "users",
              "identity", "identity_history",
              # v14 人设卡同属 L0：清库清的是经历，不是「我是谁 / 我怎么说话」
              "persona_card", "persona_card_history"}
RESET_DERIVED = ("goals", "drift_log", "self_narrative")

# 导出时补上的 v9~v11 业务表；embedding 列单独剔除
EXPORT_TABLES = (
    "events", "fragments", "bindings", "personas", "edges", "concepts",
    "concept_links", "relations", "growth_log", "goals", "drift_log",
    "self_narrative", "event_tags", "entity_alias", "emotion_state",
    "companion_state", "conversation_turns", "adoption_log", "memory_versions",
    "identity", "identity_history", "persona_card", "persona_card_history",
    # v13 皮层情感层与 Cortex 插件（运行时幂等自建，动态清库会自动纳入）
    "affect_state", "affect_relations", "affect_events",
    "proactive_suggestions", "reflection_log",
)


def _qt(name):
    """表名来自 sqlite_master，仍需正确转义防止引号破坏 SQL。"""
    return '"' + str(name).replace('"', '""') + '"'


def _reset_tables(c, scope, keep):
    names = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    if scope == "derived":
        targets = [t for t in RESET_DERIVED if t in names]
        kept = [t for t in names if t not in targets]
    else:
        targets = [t for t in names if t not in keep]
        kept = [t for t in names if t in keep]
    counts, errors = {}, {}
    for t in targets:
        try:
            counts[t] = max(0, c.execute("DELETE FROM %s" % _qt(t)).rowcount)
        except Exception as e:
            counts[t] = -1
            errors[t] = str(e)[:120]
    return targets, counts, kept, errors


@app.get("/api/export")
def export_all():
    """导出全部业务数据（不含向量，避免体积与二进制）。"""
    c = conn()
    try:
        def rows(sql):
            try:
                return [dict(r) for r in c.execute(sql)]
            except Exception:
                return []      # 该表尚未创建（例如从未生成过自叙事）

        data = {
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "note": "embedding 已省略（可由文本重建）",
            "settings": settings.public(),
        }
        for t in EXPORT_TABLES:
            data[t] = rows("SELECT * FROM %s" % _qt(t))
    finally:
        c.close()
    for key in ("events", "fragments", "concepts", "memory_versions"):
        for r in data.get(key, []):
            r.pop("embedding", None)
    return data


@app.get("/api/reset/plan")
def reset_plan(scope: str = "all"):
    """只读预检：清库会清哪些表、各有多少行、哪些保留。清库前先看一眼。"""
    if scope not in ("all", "derived"):
        return {"ok": False, "error": "scope 只能是 all / derived"}
    c = conn()
    try:
        names = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if scope == "derived":
            targets = [t for t in RESET_DERIVED if t in names]
        else:
            targets = [t for t in names if t not in RESET_KEEP]
        kept = [t for t in names if t not in targets]
        rows = {}
        for t in names:
            try:
                rows[t] = c.execute("SELECT COUNT(*) FROM %s" % _qt(t)).fetchone()[0]
            except Exception:
                rows[t] = -1
    finally:
        c.close()
    return {"ok": True, "scope": scope, "will_clear": targets,
            "will_keep": kept, "rows": rows}


@app.post("/api/reset")
def reset_all(body: Dict[str, Any]):
    """清空业务数据，保留表结构与模型 / 账号配置。

    scope=all     清所有业务表（含 v9~v11 的会话轮次、陪伴心境、L2 采纳日志、
                  再巩固版本链、标签、别名、情绪、用量），并重置自增计数。
    scope=derived 只清目标 / 漂移日志 / 自叙事，记忆与使用痕迹保留。
    可选 vacuum=true 物理重建 DB 文件（需独占，失败不影响逻辑清库）。
    返回 cleared / counts / kept / errors，便于确认新表确实被清掉了。
    """
    body = body or {}
    if body.get("confirm") != "RESET":
        return {"ok": False, "error": '需要 confirm="RESET"'}
    scope = body.get("scope") or "all"
    if scope not in ("all", "derived"):
        return {"ok": False, "error": "scope 只能是 all / derived"}
    keep = set(RESET_KEEP) | {str(x) for x in (body.get("keep") or [])}
    out = {"ok": True, "scope": scope}

    c = conn()
    try:
        targets, counts, kept, errors = _reset_tables(c, scope, keep)
        out.update(cleared=targets, counts=counts, kept=kept, errors=errors)
        if scope == "all":
            try:
                # 自增归零，但固定身份层的版本链不能跟着重置
                c.execute("DELETE FROM sqlite_sequence "
                          "WHERE name NOT LIKE 'identity%' "
                          "AND name NOT LIKE 'persona_card%'")
            except Exception:
                pass
            # 情绪回落到中性（表不存在时由 EmotionState 首次使用自建）
            try:
                c.execute("INSERT INTO emotion_state(id,valence,arousal,label) "
                          "VALUES(1,0,0,'平静')")
            except Exception:
                pass
            # v13：传同一条连接，避免 usage 模块的全局缓存连接穿透去清主库。
            try:
                usage.clear(conn=c)
                out["usage_cleared"] = True
            except Exception:
                out["usage_cleared"] = False
        c.commit()
    finally:
        c.close()

    # 进程内派生态：HTTP 服务自己的图缓存（MCP 进程由 build_graph 的 key 自行失效）
    try:
        from . import graph
        graph.invalidate()
        out["graph_invalidated"] = True
    except Exception:
        out["graph_invalidated"] = False

    # 陪伴通道的常驻单例缓存了旧库的会话 history 与人格块，清库后必须重建，
    # 否则"清完库它还记着上一轮说过的话"。
    try:
        live.reset()
        out["live_reset"] = True
    except Exception:
        out["live_reset"] = False

    # WAL 合并：清库后别把旧数据页留在 -wal 里
    try:
        c2 = conn()
        try:
            out["checkpointed"] = bool(db.checkpoint(c2, "TRUNCATE"))
        finally:
            c2.close()
    except Exception:
        out["checkpointed"] = False

    if body.get("vacuum") and scope == "all":
        try:
            c3 = conn()
            try:
                c3.execute("VACUUM")
                out["vacuumed"] = True
            finally:
                c3.close()
        except Exception as e:
            out["vacuumed"] = False
            out["vacuum_error"] = str(e)[:120]

    if out.get("errors"):
        out["ok"] = False
        out["error"] = "部分表清理失败：" + "、".join(out["errors"])
    return out


@app.post("/api/evolve")
def run_evolve(force: bool = True):
    return autoevolve.run_once(force=force)


@app.get("/api/evolve/status")
def evolve_status():
    return autoevolve.status()


@app.get("/api/usage")
def usage_summary(days: int = 7):
    return usage.summary(days)


@app.delete("/api/usage")
def usage_clear():
    usage.clear()
    return {"ok": True}


# ---------------- 管理员账号 ----------------

class LoginIn(BaseModel):
    username: str
    password: str


class AccountIn(BaseModel):
    old_password: str
    username: Optional[str] = None
    password: Optional[str] = None


def _set_session(response, username):
    response.set_cookie(account.COOKIE, account.make_token(username),
                        max_age=account.TTL, httponly=True, samesite="lax", path="/")


@app.get("/api/auth/me")
def auth_me(request: Request):
    u = auth.current_user(request)
    return {"initialized": account.initialized(), "logged_in": bool(u),
            "username": u or ""}


@app.post("/api/auth/setup")
def auth_setup(body: LoginIn, response: Response):
    r = account.create(body.username, body.password)
    if r.get("ok"):
        _set_session(response, r["username"])
    return r


@app.post("/api/auth/login")
def auth_login(body: LoginIn, response: Response):
    if not account.initialized():
        return {"ok": False, "error": "尚未创建管理员账号", "need_setup": True}
    if not account.verify(body.username, body.password):
        time.sleep(0.4)  # 拖慢暴力尝试
        return {"ok": False, "error": "用户名或密码错误"}
    name = body.username.strip()
    _set_session(response, name)
    return {"ok": True, "username": name}


@app.post("/api/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(account.COOKIE, path="/")
    return {"ok": True}


@app.post("/api/auth/account")
def auth_account(body: AccountIn, request: Request, response: Response):
    if not auth.current_user(request):
        return {"ok": False, "error": "未登录"}
    r = account.change(body.old_password, body.username, body.password)
    if r.get("ok"):
        _set_session(response, r["username"])  # 用户名可能变了，重签会话
    return r


class GoalIn(BaseModel):
    text: str


@app.get("/api/goals")
def goals_list():
    c = conn()
    try:
        from . import goals as goalsmod
        return {
            "active": goalsmod.active(c, limit=20),
            "done": [dict(r) for r in c.execute(
                "SELECT id,text,done_at FROM goals WHERE status='done' "
                "ORDER BY done_at DESC LIMIT 10")],
        }
    finally:
        c.close()


@app.post("/api/goals")
def goals_add(body: GoalIn):
    c = conn()
    try:
        from . import goals as goalsmod
        text = (body.text or "").strip()
        if len(text) < 3:
            return {"ok": False, "error": "目标至少 3 个字"}
        gid = goalsmod.add(c, text)
        c.commit()
        return {"ok": True, "id": gid}
    finally:
        c.close()


@app.post("/api/goals/{gid}/done")
def goals_done(gid: str):
    c = conn()
    try:
        from . import goals as goalsmod
        r = goalsmod.complete(c, gid)
        c.commit()
        return r
    finally:
        c.close()


@app.delete("/api/goals/{gid}")
def goals_drop(gid: str):
    c = conn()
    try:
        from . import goals as goalsmod
        r = goalsmod.drop(c, gid)
        c.commit()
        return r
    finally:
        c.close()


class RetrieveIn(BaseModel):
    query: str
    top_k: int = 10
    subject: Optional[str] = None   # v12：只看某个主体的记忆


@app.post("/api/retrieve")
def retrieve(body: RetrieveIn):
    """纯检索接口：给 agent 用，只返回记忆与分数，不调用 LLM 生成。"""
    c = conn()
    try:
        t0 = time.perf_counter()
        info = {}
        items = recallmod.recall(c, body.query, top_k=max(1, min(body.top_k, 50)), info=info)
        events, concepts = [], []
        for it in items:
            if body.subject and (it.get("subject") or config.DEFAULT_SUBJECT) != body.subject:
                continue
            row = {
                "id": it["id"], "type": it.get("type", "event"), "gist": it["gist"],
                "subject": it.get("subject") or config.DEFAULT_SUBJECT,
                "kind": it.get("kind"), "score": it.get("score"),
                "scores": {"vec": it.get("vec"), "kw": it.get("kw")},
                "occurred_at": it.get("occurred_at") or "",
                "created_at": it.get("created_at") or "",
                "place": it.get("place") or "",
                "affect": it.get("affect") or {},
                "working": bool(it.get("working")),
            }
            (concepts if it.get("type") == "concept" else events).append(row)
        return {
            "ok": True,
            "query": body.query,
            "events": events,
            "concepts": concepts,
            "relations": querymod._relation_context(c, body.query),
            "timings": {"recall_ms": round((time.perf_counter() - t0) * 1000, 1)},
            "warnings": (["embed_error: " + info["embed_error"]]
                         if info.get("embed_error") else []),
        }
    finally:
        c.close()


@app.get("/api/health")
def health():
    cur = settings.current()
    p = Path(config.DB_PATH)
    return {
        "db_path": str(p),
        "db_size": p.stat().st_size if p.exists() else 0,
        "provider": cur["llm"].get("provider"),
        "extract_model": cur["llm"].get("extract_model"),
        "answer_model": cur["llm"].get("answer_model"),
        "embed_provider": cur["embed"].get("provider"),
        "embed_model": cur["embed"].get("model"),
        "embed_dim": cur["embed"].get("dim"),
    }


@app.get("/")
def index():
    return FileResponse(str(WEB_DIR / "index.html"))


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")