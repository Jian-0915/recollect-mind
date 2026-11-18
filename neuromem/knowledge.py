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
