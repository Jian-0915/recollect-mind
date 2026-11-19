"""独立外部知识库 —— 与陪伴记忆物理隔离。

红线（ROADMAP 自进化章节）：
  - 独立 SQLite 文件（默认 data/knowledge.db），**绝不写主库 neuromem.db**
    的 events / concepts / fragments 等任何表。
  - 每条知识带 URL、来源 provider、抓取时间、原始查询意图，可追溯。
  - 只用 FTS5(trigram) 做中文子串全文索引 + 可选向量列做语义召回，
    两路结果 RRF 融合；FTS5 不可用时降级 LIKE + bigram 打分。

为什么独立 DB 而不是主库独立表：用户明确要求「外部信息不污染陪伴记忆」。
物理隔离比约定更可靠——就算将来有人误写，也碰不到主库一行。
"""
from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import config, db as nm_db

KNOWLEDGE_DB = Path(os.environ.get("NEUROMEM_KNOWLEDGE_DB")
                    or (config.DATA_DIR / "knowledge.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_docs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  title       TEXT NOT NULL,
  url         TEXT NOT NULL UNIQUE,
  snippet     TEXT NOT NULL DEFAULT '',
  content     TEXT NOT NULL DEFAULT '',
  query       TEXT NOT NULL DEFAULT '',
  provider    TEXT NOT NULL DEFAULT 'anysearch',
  request_id  TEXT NOT NULL DEFAULT '',
  fetched_at  TEXT NOT NULL,
  embedding   BLOB
);
CREATE INDEX IF NOT EXISTS idx_knowledge_fetched ON knowledge_docs(fetched_at DESC);

-- 自主查询审计日志：她自己发现缺口去查时留痕（何时/为何/查了什么/结果几条）。
-- 同样只存在独立库，绝不写主库。
CREATE TABLE IF NOT EXISTS autoresearch_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  entity        TEXT NOT NULL DEFAULT '',
  query         TEXT NOT NULL DEFAULT '',
  reason        TEXT NOT NULL DEFAULT '',
  hit_count     INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'ok',
  source_event  TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autoresearch_created ON autoresearch_log(created_at DESC);

-- 知识播报队列：F5 查到资料后，用对话模型整理成一句自然的话写这里，
-- 前端聊天页轮询取走、显示成「她自己查到的」一条消息。展示即消费。
-- 只存独立库：播报只是知识的一种呈现，绝不写主库 events/concepts。
CREATE TABLE IF NOT EXISTS discovery_brief (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  entity      TEXT NOT NULL DEFAULT '',
  text        TEXT NOT NULL DEFAULT '',
  sources     TEXT NOT NULL DEFAULT '[]',   -- JSON: [{title,url}]
  delivered   INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_brief_created ON discovery_brief(created_at DESC);

-- 待查队列（v45）：她许下「我去查查」或发现没聊完的话题时先记在这里，
-- 由常驻循环异步兑现——不依赖对方再发一句话（不走被动路径）。
-- status: pending 待兑现 / done 已查过（成功或查空）/ 只增不改以免反复查。
-- 同样只存独立库：队列是查询意图，不是陪伴记忆。
CREATE TABLE IF NOT EXISTS research_backlog (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  entity        TEXT NOT NULL DEFAULT '',
  reason        TEXT NOT NULL DEFAULT '',
  source_event  TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'pending',
  attempts      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backlog_status ON research_backlog(status,id);
CREATE INDEX IF NOT EXISTS idx_backlog_entity ON research_backlog(entity,created_at);
"""


def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _db_path(path=None) -> Path:
    """运行时读 env —— 测试 monkeypatch 环境变量即可隔离，不会误写主库。"""
    return Path(path or os.environ.get("NEUROMEM_KNOWLEDGE_DB")
                or KNOWLEDGE_DB)


def connect(path=None) -> sqlite3.Connection:
    p = _db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(SCHEMA)
    _ensure_fts(conn)
    return conn


def _ensure_fts(conn: sqlite3.Connection) -> bool:
    """建 trigram 全文索引；不支持时静默降级为 LIKE 检索。"""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts "
                     "USING fts5(title, snippet, content, tokenize='trigram')")
        return True
    except sqlite3.Error:
        return False


def _fts_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT rowid FROM knowledge_fts LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


@dataclass
class KnowledgeHit:
    id: int
    title: str
    url: str
    snippet: str
    score: float
    fetched_at: str
    provider: str = "anysearch"

    def as_prompt(self, index: int = 1) -> str:
        parts = [f"[{index}] {self.title}（来源：{self.url}，抓取：{self.fetched_at}）"]
        if self.snippet:
            parts.append(self.snippet[:1000])
        return "\n".join(parts)


# --------------------------------------------------------------------------
# 写入
# --------------------------------------------------------------------------

def add_results(conn: sqlite3.Connection, results: Iterable, *,
                query: str = "", provider: str = "anysearch",
                embed_text: bool = True) -> tuple[int, int]:
    """把搜索结果存进知识库。返回 (新增数, 更新数)。

    同一 URL 重复抓取时更新正文与时间，不产生新条目。
    embedding 失败不影响写入——语义召回是加分项，不是必需项。
    """
    added = updated = 0
    now = _iso_now()
    fts = _fts_available(conn)

    for r in results:
        url = (getattr(r, "url", "") or "").strip()
        title = (getattr(r, "title", "") or "无标题").strip()
        snippet = (getattr(r, "snippet", "") or "").strip()
        req_id = getattr(r, "request_id", "") or ""
        if not url:
            continue

        vec = None
        if embed_text:
            vec = _safe_embed(f"{title}\n{snippet}")

        old = conn.execute("SELECT id FROM knowledge_docs WHERE url=?", (url,)).fetchone()
        if old:
            doc_id = old["id"]
            conn.execute(
                "UPDATE knowledge_docs SET title=?,snippet=?,query=?,provider=?,"
                "request_id=?,fetched_at=?,embedding=COALESCE(?,embedding) WHERE id=?",
                (title, snippet, query, provider, req_id, now, vec, doc_id))
            updated += 1
        else:
            cur = conn.execute(
                "INSERT INTO knowledge_docs(title,url,snippet,content,query,provider,"
                "request_id,fetched_at,embedding) VALUES (?,?,?,?,?,?,?,?,?)",
                (title, url, snippet, "", query, provider, req_id, now, vec))
            doc_id = cur.lastrowid
            added += 1

        if fts:
            conn.execute("DELETE FROM knowledge_fts WHERE rowid=?", (doc_id,))
            conn.execute("INSERT INTO knowledge_fts(rowid,title,snippet,content) "
                         "VALUES (?,?,?,?)", (doc_id, title, snippet, ""))

    conn.commit()
    return added, updated


def set_content(conn: sqlite3.Connection, url: str, content: str) -> bool:
    """把 extract 到的全文补进已有条目（可选，控制体积）。"""
    row = conn.execute("SELECT id FROM knowledge_docs WHERE url=?", (url,)).fetchone()
    if not row:
        return False
    conn.execute("UPDATE knowledge_docs SET content=? WHERE id=?",
                 (content[:20000], row["id"]))
    if _fts_available(conn):
        conn.execute("DELETE FROM knowledge_fts WHERE rowid=?", (row["id"],))
        conn.execute("INSERT INTO knowledge_fts(rowid,title,snippet,content) "
                     "SELECT id,title,snippet,content FROM knowledge_docs WHERE id=?",
                     (row["id"],))
    conn.commit()
    return True


def _safe_embed(text: str):
    try:
        from . import embed
        v = embed.embed(text)
        return nm_db.pack(v) if v is not None else None
    except Exception:
        return None


# --------------------------------------------------------------------------
# 检索
# --------------------------------------------------------------------------

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _bigrams(text: str) -> set[str]:
    """中文 bigram + 英文/数字词，作为轻量关键词指纹。"""
    s = (text or "").lower()
    out = set(re.findall(r"[a-z0-9]{2,}", s))
    cjk = "".join(_CJK.findall(s))
    out.update(cjk[i:i + 2] for i in range(len(cjk) - 1))
    if len(cjk) == 1:
        out.add(cjk)
    return out


def _keyword_score(query: str, text: str) -> float:
    q, t = _bigrams(query), _bigrams(text)
    if not q or not t:
        return 0.0
    return len(q & t) / len(q)


def search(conn: sqlite3.Connection, query: str, top_k: int = 5,
           vector=None) -> list[KnowledgeHit]:
    """关键词 + 向量双路检索，RRF 融合。

    vector 为已编码的查询向量（可选）；不传则纯关键词，不会触发 embed 网络调用。
    """
    query = (query or "").strip()
    if not query:
        return []

    rows = conn.execute(
        "SELECT id,title,url,snippet,fetched_at,provider,embedding "
        "FROM knowledge_docs ORDER BY id DESC LIMIT 500").fetchall()
    if not rows:
        return []

    fts_hits: set[int] = set()
    if _fts_available(conn) and len(_bigrams(query)):
        # trigram 要求 >=3 字符；长查询直接走 FTS，短查询用 LIKE 兜底
        terms = [w for w in re.split(r"\s+", query) if len(w) >= 3]
        if terms:
            fts_expr = " OR ".join('"' + w.replace('"', '""') + '"' for w in terms[:4])
            try:
                fts_hits = {r[0] for r in conn.execute(
                    "SELECT rowid FROM knowledge_fts WHERE knowledge_fts MATCH ? LIMIT 200",
                    (fts_expr,)).fetchall()}
            except sqlite3.Error:
                fts_hits = set()
        if not fts_hits:
            like = f"%{query[:40]}%"
            fts_hits = {r["id"] for r in rows
                        if query[:40] in (r["title"] + r["snippet"])}

    qb = _bigrams(query)
    kw_scores: dict[int, float] = {}
    for r in rows:
        text = f"{r['title']} {r['snippet']}"
        s = _keyword_score(query, text)
        if r["id"] in fts_hits:
            s += 0.5
        if s > 0:
            kw_scores[r["id"]] = s

    vec_scores: dict[int, float] = {}
    if vector is not None:
        for r in rows:
            if not r["embedding"]:
                continue
            try:
                v = nm_db.unpack(r["embedding"])
                vec_scores[r["id"]] = _cos(vector, v)
            except Exception:
                continue

    def rrf(rank: dict[int, float], k: int = 60) -> dict[int, float]:
        out = {}
        for pos, rid in enumerate(sorted(rank, key=rank.get, reverse=True), start=1):
            out[rid] = 1.0 / (k + pos)
        return out

    fused: dict[int, float] = {}
    for rid, score in rrf(kw_scores).items():
        fused[rid] = fused.get(rid, 0) + score * 1.0
    for rid, score in rrf(vec_scores).items():
        fused[rid] = fused.get(rid, 0) + score * 0.8

    by_id = {r["id"]: r for r in rows}
    hits = []
    for rid, score in sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k]:
        r = by_id[rid]
        hits.append(KnowledgeHit(
            id=rid, title=r["title"], url=r["url"], snippet=r["snippet"],
            score=round(score, 5), fetched_at=r["fetched_at"], provider=r["provider"]))
    return hits


def recall(conn: sqlite3.Connection, text: str, entities=(), top_k: int = 5) -> list[KnowledgeHit]:
    """多路召回：整句 + 每个实体分别检索，按评分融合去重。

    对话时用它判断「她自己查过的知识里，有没有和这句相关的」。整句能抓到
    描述性表达（「上次那个古镇」），实体能抓到专名（「乌江寨」），两路互补。
    只读独立库、零网络——vector 不传，不会触发 embed。
    """
    queries: list[str] = []
    whole = (text or "").strip()
    if whole:
        queries.append(whole)
    for e in (entities or ()):
        e = str(e).strip()
        if len(e) >= 2 and e not in queries:
            queries.append(e)
    fused: dict[int, KnowledgeHit] = {}
    for q in queries[:6]:                    # 最多 6 路，防 prompt/CPU 膨胀
        for h in search(conn, q, top_k=max(top_k, 3)):
            prev = fused.get(h.id)
            # 命中多路说明更相关，累加分数提权；保留信息最全的一条
            if prev is None:
                fused[h.id] = h
            else:
                prev.score = round(prev.score + h.score, 5)
    scored = sorted(fused.values(), key=lambda h: h.score, reverse=True)
    return scored[:top_k]


def _cos(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def has_knowledge(conn: sqlite3.Connection, entity: str) -> bool:
    """触发闸用：知识库里是否已有关于这个实体的可靠信息。"""
    if not entity or len(entity.strip()) < 2:
        return False
    return bool(search(conn, entity, top_k=1))


def list_recent(conn: sqlite3.Connection, limit: int = 20) -> list[KnowledgeHit]:
    rows = conn.execute(
        "SELECT id,title,url,snippet,fetched_at,provider FROM knowledge_docs "
        "ORDER BY fetched_at DESC LIMIT ?", (limit,)).fetchall()
    return [KnowledgeHit(r["id"], r["title"], r["url"], r["snippet"], 0.0,
                         r["fetched_at"], r["provider"]) for r in rows]


def stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS n, MAX(fetched_at) AS latest FROM knowledge_docs").fetchone()
    return {"count": row["n"] or 0, "latest": row["latest"] or ""}


# --------------------------------------------------------------------------
# 自主查询审计日志（F5）：只在独立库，记录她自己发现缺口去查的每一次
# --------------------------------------------------------------------------

def log_autoresearch(conn: sqlite3.Connection, *, entity: str = "", query: str = "",
                     reason: str = "", hit_count: int = 0, status: str = "ok",
                     source_event: str = "") -> int:
    """写一条自主查询审计。返回日志 id。任何异常都不外抛（审计不能挡住对话）。"""
    try:
        cur = conn.execute(
            "INSERT INTO autoresearch_log"
            "(entity,query,reason,hit_count,status,source_event,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (str(entity)[:60], str(query)[:120], str(reason)[:120],
             int(hit_count or 0), str(status)[:24], str(source_event)[:120],
             _iso_now()))
        conn.commit()
        return int(cur.lastrowid or 0)
    except sqlite3.Error:
        return 0


def list_autoresearch(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        "SELECT id,entity,query,reason,hit_count,status,source_event,created_at "
        "FROM autoresearch_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def autoresearch_count_since(conn: sqlite3.Connection, iso_ts: str) -> int:
    """某时间点以来的自主查询次数（频次上限用）。字符串比较 ISO 时间即可。"""
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM autoresearch_log WHERE created_at >= ?",
            (iso_ts,)).fetchone()
        return int(row["n"] or 0)
    except sqlite3.Error:
        return 0


def autoresearch_last_at(conn: sqlite3.Connection) -> str:
    """最近一次自主查询时间（间隔闸门用），没有返回空串。"""
    try:
        row = conn.execute(
            "SELECT created_at FROM autoresearch_log ORDER BY id DESC LIMIT 1").fetchone()
        return str(row["created_at"] or "") if row else ""
    except sqlite3.Error:
        return ""


# --------------------------------------------------------------------------
# 知识播报队列：F5 查到 → 模型整理 → 写这里 → 前端聊天页取走显示
# --------------------------------------------------------------------------

def add_brief(conn: sqlite3.Connection, *, entity: str = "", text: str = "",
              sources=None) -> int:
    """写一条待播报的知识短句。sources 为 [{title,url,...}]，存 JSON。返回 id。"""
    import json
    text = str(text or "").strip()
    if not text:
        return 0
    try:
        cur = conn.execute(
            "INSERT INTO discovery_brief(entity,text,sources,delivered,created_at) "
            "VALUES(?,?,?,0,?)",
            (str(entity)[:60], text[:600],
             json.dumps(sources or [], ensure_ascii=False)[:4000], _iso_now()))
        conn.commit()
        return int(cur.lastrowid or 0)
    except sqlite3.Error:
        return 0


def peek_brief(conn: sqlite3.Connection) -> dict | None:
    """取一条未播报的知识短句（只标记 delivered，不删除，留审计）。无则 None。"""
    try:
        row = conn.execute(
            "SELECT id,entity,text,sources,created_at FROM discovery_brief "
            "WHERE delivered=0 ORDER BY id ASC LIMIT 1").fetchone()
        if not row:
            return None
        conn.execute("UPDATE discovery_brief SET delivered=1 WHERE id=?", (row["id"],))
        conn.commit()
        out = dict(row)
        try:
            import json
            out["sources"] = json.loads(out.get("sources") or "[]")
        except Exception:
            out["sources"] = []
        out["created_at"] = out.get("created_at") or ""
        return out
    except sqlite3.Error:
        return None


def brief_stats(conn: sqlite3.Connection) -> dict:
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN delivered=0 THEN 1 ELSE 0 END) AS pending "
            "FROM discovery_brief").fetchone()
        return {"total": int(row["n"] or 0), "pending": int(row["pending"] or 0)}
    except sqlite3.Error:
        return {"total": 0, "pending": 0}


# --------------------------------------------------------------------------
# 待查队列（v45）：承诺/未聊完的话题 → 排队 → 常驻循环异步兑现
# --------------------------------------------------------------------------

def enqueue_research(conn: sqlite3.Connection, *, entity: str, reason: str = "",
                     source_event: str = "", dedup_hours: int = 48) -> int:
    """排一个待查主题，返回任务 id；重复或为空返回 0。

    去重按「同一 entity 最近 N 小时已经在队列里 / 已查过」算——她说了十遍
    「我去查查」也只查一次。队列只存在独立库，绝不写主库。
    """
    entity = str(entity or "").strip()[:60]
    if len(entity) < 2:
        return 0
    try:
        since = (datetime.now(timezone.utc).astimezone()
                 - __import__("datetime").timedelta(hours=dedup_hours)).isoformat()
        dup = conn.execute(
            "SELECT 1 FROM research_backlog WHERE entity=? AND created_at>=? LIMIT 1",
            (entity, since)).fetchone()
        if dup:
            return 0
        cur = conn.execute(
            "INSERT INTO research_backlog(entity,reason,source_event,status,attempts,created_at) "
            "VALUES(?,?,?,'pending',0,?)",
            (entity, str(reason or "")[:120], str(source_event or "")[:120], _iso_now()))
        conn.commit()
        return int(cur.lastrowid or 0)
    except sqlite3.Error:
        return 0


def pending_research(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        "SELECT id,entity,reason,source_event,attempts,created_at "
        "FROM research_backlog WHERE status='pending' ORDER BY id ASC LIMIT ?",
        (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def mark_research(conn: sqlite3.Connection, task_id: int, *, status: str = "done") -> bool:
    try:
        conn.execute("UPDATE research_backlog SET status=? WHERE id=?",
                     (str(status)[:24], int(task_id)))
        conn.commit()
        return True
    except sqlite3.Error:
        return False


def bump_research(conn: sqlite3.Connection, task_id: int) -> int:
    """查询失败/被闸门挡下时记一次尝试；超过上限就不要反复撞配额。"""
    try:
        conn.execute("UPDATE research_backlog SET attempts=attempts+1 WHERE id=?",
                     (int(task_id),))
        conn.commit()
        row = conn.execute("SELECT attempts FROM research_backlog WHERE id=?",
                           (int(task_id),)).fetchone()
        return int(row["attempts"] or 0) if row else 0
    except sqlite3.Error:
        return 0


def backlog_stats(conn: sqlite3.Connection) -> dict:
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, "
            "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending, "
            "SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done, "
            "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed "
            "FROM research_backlog").fetchone()
        return {"total": int(row["n"] or 0), "pending": int(row["pending"] or 0),
                "done": int(row["done"] or 0), "failed": int(row["failed"] or 0)}
    except sqlite3.Error:
        return {"total": 0, "pending": 0, "done": 0, "failed": 0}


# --------------------------------------------------------------------------
# 系统运行视图（v47）：只读列表，给「系统运行」界面看全貌，不改任何状态。
# --------------------------------------------------------------------------

def list_backlog(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """待查队列全量（含 done/failed），按 id 倒序，供运行界面看历史。"""
    try:
        rows = conn.execute(
            "SELECT id,entity,reason,source_event,status,attempts,created_at "
            "FROM research_backlog ORDER BY id DESC LIMIT ?",
            (min(max(int(limit), 1), 200),)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def list_briefs(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """全部播报（含已投递），text 截断——界面只做一览，不塞整段草稿。"""
    try:
        rows = conn.execute(
            "SELECT id,entity,substr(text,1,160) AS text,sources,delivered,created_at "
            "FROM discovery_brief ORDER BY id DESC LIMIT ?",
            (min(max(int(limit), 1), 200),)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                import json
                d["sources"] = json.loads(d.get("sources") or "[]")[:3]
            except Exception:
                d["sources"] = []
            out.append(d)
        return out
    except sqlite3.Error:
        return []
