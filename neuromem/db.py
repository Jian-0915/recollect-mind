"""SQLite 数据层：建表、迁移、连接、向量打包/解包。"""
import sqlite3
import struct
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  created_at TEXT,
  occurred_at TEXT,
  time_text TEXT,
  place TEXT,
  gist TEXT,
  affect_json TEXT,
  salience REAL,
  embedding BLOB,
  confidence REAL,
  provenance TEXT,
  consolidation_stage TEXT,
  activation REAL DEFAULT 1.0,
  last_recalled_at TEXT,
  archived INTEGER DEFAULT 0,
  surprise REAL,
  last_try_at TEXT,
  superseded_by TEXT,
  recall_count INTEGER DEFAULT 0,
  last_decay_at TEXT,
  revision_count INTEGER DEFAULT 0,
  last_reconsolidated_at TEXT,
  search_text TEXT,
  subject TEXT NOT NULL DEFAULT 'counterpart',
  fact_key TEXT
);
CREATE TABLE IF NOT EXISTS fragments (
  id TEXT PRIMARY KEY,
  text TEXT,
  modality TEXT,
  embedding BLOB,
  salience REAL
);
CREATE TABLE IF NOT EXISTS bindings (
  event_id TEXT,
  fragment_id TEXT,
  role TEXT,
  seq INTEGER
);
CREATE TABLE IF NOT EXISTS personas (
  id TEXT PRIMARY KEY,
  canonical_name TEXT,
  aliases_json TEXT,
  profile TEXT,
  profile_at TEXT,
  profile_events INTEGER DEFAULT 0,
  kind TEXT DEFAULT 'person',
  bond TEXT DEFAULT 'social'
);
CREATE TABLE IF NOT EXISTS edges (
  source TEXT,
  target TEXT,
  type TEXT,
  weight REAL
);
CREATE TABLE IF NOT EXISTS concepts (
  id TEXT PRIMARY KEY,
  text TEXT,
  abstraction_level INTEGER,
  confidence REAL,
  created_at TEXT,
  embedding BLOB
);
CREATE TABLE IF NOT EXISTS concept_links (
  concept_id TEXT,
  event_id TEXT,
  type TEXT
);
CREATE TABLE IF NOT EXISTS relations (
  id TEXT PRIMARY KEY,
  a TEXT,
  b TEXT,
  rel_type TEXT,
  strength REAL DEFAULT 0.5,
  since TEXT,
  until TEXT,
  evidence TEXT
);
CREATE TABLE IF NOT EXISTS growth_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  events INTEGER,
  active_events INTEGER,
  concepts INTEGER,
  max_level INTEGER,
  archived INTEGER
);
CREATE TABLE IF NOT EXISTS goals (
  id TEXT PRIMARY KEY,
  text TEXT,
  status TEXT DEFAULT 'active',
  created_at TEXT,
  done_at TEXT,
  source_event TEXT
);
CREATE TABLE IF NOT EXISTS event_tags (
  event_id TEXT NOT NULL,
  tag TEXT NOT NULL,
  source TEXT DEFAULT 'auto',
  PRIMARY KEY (event_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_event_tags_tag ON event_tags(tag);
CREATE TABLE IF NOT EXISTS entity_alias (
  alias TEXT NOT NULL,
  target TEXT NOT NULL,
  kind TEXT DEFAULT 'category',
  PRIMARY KEY (alias, target)
);
CREATE TABLE IF NOT EXISTS drift_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  n INTEGER,
  magnitude REAL
);
CREATE INDEX IF NOT EXISTS idx_bind_event ON bindings(event_id);
CREATE INDEX IF NOT EXISTS idx_edge_source ON edges(source);
CREATE INDEX IF NOT EXISTS idx_edge_target ON edges(target);
CREATE INDEX IF NOT EXISTS idx_clink_event ON concept_links(event_id);
CREATE TABLE IF NOT EXISTS companion_state (
  id INTEGER PRIMARY KEY CHECK(id=1),
  last_turn_at TEXT,
  last_topic TEXT,
  open_topics TEXT,
  address_style TEXT,
  boundaries TEXT,
  surfaced_ids TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS conversation_turns (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  ts TEXT,
  speaker TEXT,
  text TEXT,
  gist TEXT,
  affect_json TEXT,
  event_ids TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_turn_ts ON conversation_turns(ts);
CREATE TABLE IF NOT EXISTS adoption_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  query TEXT,
  source TEXT,
  candidate_ids TEXT,
  prompt_ids TEXT,
  used_ids TEXT,
  rejected_ids TEXT,
  summary TEXT,
  outcome TEXT,
  weight REAL
);
CREATE INDEX IF NOT EXISTS idx_adopt_ts ON adoption_log(ts);
CREATE TABLE IF NOT EXISTS memory_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL,
  revision INTEGER,
  gist TEXT,
  embedding BLOB,
  affect_json TEXT,
  reason TEXT,
  query TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mv_event ON memory_versions(event_id, revision);
CREATE TABLE IF NOT EXISTS identity (
  id INTEGER PRIMARY KEY CHECK(id=1),
  version INTEGER NOT NULL DEFAULT 1,
  profile_json TEXT NOT NULL,
  checksum TEXT NOT NULL,
  updated_at TEXT,
  updated_by TEXT DEFAULT 'human'
);
CREATE TABLE IF NOT EXISTS identity_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version INTEGER,
  profile_json TEXT,
  checksum TEXT,
  updated_at TEXT,
  updated_by TEXT,
  reason TEXT
);
"""


def _migrate(conn):
    """给旧库补列/补表。"""
    ecols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    alters = []
    if "activation" not in ecols:
        alters.append("ALTER TABLE events ADD COLUMN activation REAL DEFAULT 1.0")
    if "last_recalled_at" not in ecols:
        alters.append("ALTER TABLE events ADD COLUMN last_recalled_at TEXT")
    if "archived" not in ecols:
        alters.append("ALTER TABLE events ADD COLUMN archived INTEGER DEFAULT 0")
    if "surprise" not in ecols:
        alters.append("ALTER TABLE events ADD COLUMN surprise REAL")
    if "last_try_at" not in ecols:
        alters.append("ALTER TABLE events ADD COLUMN last_try_at TEXT")
    if "superseded_by" not in ecols:
        alters.append("ALTER TABLE events ADD COLUMN superseded_by TEXT")
    if "recall_count" not in ecols:
        alters.append("ALTER TABLE events ADD COLUMN recall_count INTEGER DEFAULT 0")
    # v11：时间锚点遗忘 + 再巩固版本链
    if "last_decay_at" not in ecols:
        alters.append("ALTER TABLE events ADD COLUMN last_decay_at TEXT")
    if "revision_count" not in ecols:
        alters.append("ALTER TABLE events ADD COLUMN revision_count INTEGER DEFAULT 0")
    if "last_reconsolidated_at" not in ecols:
        alters.append("ALTER TABLE events ADD COLUMN last_reconsolidated_at TEXT")
    if "search_text" not in ecols:
        alters.append("ALTER TABLE events ADD COLUMN search_text TEXT")
    # v12：主体归属。存量记忆一律标 counterpart（旧系统记录的是对方），
    # 不改 gist、不删数据；之后由 agent 显式写入 self / shared。
    if "subject" not in ecols:
        alters.append(
            "ALTER TABLE events ADD COLUMN subject TEXT NOT NULL DEFAULT 'counterpart'")
    # v13：结构化事实键（谁·什么属性·什么值）。存量留空，由回填脚本补；
    # 抽不出的保持 NULL —— store 会自动退回文本判据，不会因为没键就漏判。
    if "fact_key" not in ecols:
        alters.append("ALTER TABLE events ADD COLUMN fact_key TEXT")
    ccols = {r["name"] for r in conn.execute("PRAGMA table_info(concepts)")}
    if "embedding" not in ccols:
        alters.append("ALTER TABLE concepts ADD COLUMN embedding BLOB")
    # v6：画像自动刷新需要记录"上次生成时的经历条数"
    pcols = {r["name"] for r in conn.execute("PRAGMA table_info(personas)")}
    if "profile_at" not in pcols:
        alters.append("ALTER TABLE personas ADD COLUMN profile_at TEXT")
    if "profile_events" not in pcols:
        alters.append("ALTER TABLE personas ADD COLUMN profile_events INTEGER DEFAULT 0")
    # v19：实体类型（person/pet/group/time/place/other）。存量默认 person——
    # 保守：宁可暂时把「金毛」当人，也不能把真人误标成非人而踢出画像。
    # 真实分类由 tools/backfill_entities.py 回填。
    if "kind" not in pcols:
        alters.append("ALTER TABLE personas ADD COLUMN kind TEXT DEFAULT 'person'")
    # v20：关系性质（social / mentioned）。kind 回答「是不是人」，bond 回答
    # 「是不是我认识的」。存量默认 social，由 tools/backfill_entities.py 回填。
    if "bond" not in pcols:
        alters.append("ALTER TABLE personas ADD COLUMN bond TEXT DEFAULT 'social'")
    for a in alters:
        conn.execute(a)
    if "subject" in {r["name"] for r in conn.execute("PRAGMA table_info(events)")}:
        conn.execute(
            "UPDATE events SET subject='counterpart' "
            "WHERE subject IS NULL OR subject=''")
    # subject 索引必须放在 ALTER 之后：v11 旧库执行 SCHEMA 时 events 还没有
    # 这一列，若把 CREATE INDEX 写进 SCHEMA 会在迁移前直接报错。
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_subject ON events(subject)")
    except sqlite3.Error:
        pass
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_factkey ON events(fact_key)")
    except sqlite3.Error:
        pass
    if alters:
        conn.commit()


SCHEMA_VERSION = 20  # v20：personas.bond 关系性质（v19 kind / v13 fact_key）


def connect(path=None):
    p = Path(path or config.DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False：Memory SDK 常被 async agent 在事件循环与工作线程间
    # 复用同一个连接，默认 True 会抛 ProgrammingError。连接可跨线程共享（Python 3.11+
    # sqlite3 序列化模式），但 cursor 仍不要跨线程传；并发写由 WAL + busy_timeout 兜底。
    conn = sqlite3.connect(str(p), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # 并发友好：WAL + 忙等，避免自动进化与写入互相 database is locked
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
    except sqlite3.Error:
        pass
    # 只在结构缺失/版本落后时执行建表与迁移。
    # 每个请求都 executescript(SCHEMA) 会抢写锁，8 线程并发时把召回拖到 3 秒。
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone()
    except sqlite3.Error:
        ver, exists = 0, None
    if not exists or ver < SCHEMA_VERSION:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
        conn.commit()
    return conn


def checkpoint(conn, mode="TRUNCATE"):
    """把 WAL 合并回主库。短连接频繁写时 WAL 会一直涨，需要定期 checkpoint。

    返回是否完成合并；TRUNCATE 需要独占，遇读者占用时退化为 PASSIVE
    （不阻塞写入，能推进多少算多少，下次再试）。
    """
    try:
        row = conn.execute("PRAGMA wal_checkpoint(%s)" % mode).fetchone()
        if row and not row[0]:
            return True
        row = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        return bool(row) and not row[0]
    except Exception:
        return False


def pack(vec):
    if vec is None:
        return None
    return struct.pack("%df" % len(vec), *[float(x) for x in vec])


def unpack(blob):
    if not blob:
        return None
    n = len(blob) // 4
    return list(struct.unpack("%df" % n, blob))