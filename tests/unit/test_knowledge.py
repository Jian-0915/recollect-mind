"""独立知识库测试：验证物理隔离、中文检索、去重与降级。"""
import sqlite3

import pytest

from neuromem import db as nm_db, knowledge


class FakeResult:
    def __init__(self, title, url, snippet, request_id="r1"):
        self.title, self.url, self.snippet = title, url, snippet
        self.request_id = request_id


@pytest.fixture
def conn(tmp_path):
    c = knowledge.connect(tmp_path / "kb.db")
    yield c
    c.close()


def test_uses_separate_db_file(tmp_path):
    p = tmp_path / "kb.db"
    c = knowledge.connect(p)
    c.close()
    assert p.exists() and p.name != "neuromem.db"


def test_add_and_search_chinese(conn):
    added, updated = knowledge.add_results(conn, [
        FakeResult("乌江寨官网", "https://wujiang.example",
                   "贵州乌江寨国际旅游度假区，吊脚楼与非遗打铁花"),
        FakeResult("无关页面", "https://other.example", "今天天气不错"),
    ], query="乌江寨", embed_text=False)
    assert (added, updated) == (2, 0)

    hits = knowledge.search(conn, "乌江寨")
    assert hits and hits[0].url == "https://wujiang.example"
    assert "乌江寨" in hits[0].title
    assert hits[0].fetched_at
    assert knowledge.has_knowledge(conn, "乌江寨")


def test_dedup_by_url(conn):
    knowledge.add_results(conn, [FakeResult("旧标题", "https://a.example", "旧摘要")],
                          embed_text=False)
    a, u = knowledge.add_results(conn,
                                 [FakeResult("新标题", "https://a.example", "新摘要")],
                                 embed_text=False)
    assert (a, u) == (0, 1)
    assert conn.execute("SELECT COUNT(*) FROM knowledge_docs").fetchone()[0] == 1
    row = conn.execute("SELECT title,snippet FROM knowledge_docs").fetchone()
    assert row["title"] == "新标题" and row["snippet"] == "新摘要"


def test_fts_trigram_index(conn):
    assert knowledge._fts_available(conn)
    knowledge.add_results(conn, [FakeResult("乌江寨攻略", "https://b.example",
                                            "完整保留黔北吊脚楼建筑群")], embed_text=False)
    ids = {r[0] for r in conn.execute(
        "SELECT rowid FROM knowledge_fts WHERE knowledge_fts MATCH '乌江寨'")}
    assert 1 in ids


def test_stats_and_recent(conn):
    knowledge.add_results(conn, [FakeResult("条目", "https://c.example", "摘要")],
                          embed_text=False)
    s = knowledge.stats(conn)
    assert s["count"] == 1 and s["latest"]
    recent = knowledge.list_recent(conn)
    assert len(recent) == 1 and recent[0].url == "https://c.example"


def test_no_knowledge(conn):
    assert not knowledge.has_knowledge(conn, "完全没收录的东西")
    assert knowledge.search(conn, "") == []


def test_env_override_is_read_at_connect_time(tmp_path, monkeypatch):
    target = tmp_path / "from_env.db"
    monkeypatch.setenv("NEUROMEM_KNOWLEDGE_DB", str(target))
    c = knowledge.connect()
    c.close()
    assert target.exists()


def test_set_content(conn):
    knowledge.add_results(conn, [FakeResult("文章", "https://d.example", "摘要")],
                          embed_text=False)
    assert knowledge.set_content(conn, "https://d.example", "全文内容" * 10)
    row = conn.execute("SELECT content FROM knowledge_docs WHERE url=?",
                       ("https://d.example",)).fetchone()
    assert "全文内容" in row["content"]


def test_main_db_untouched(tmp_path):
    """红线：写外部知识后，主库 events/knowledge 相关表一行不变。"""
    main = tmp_path / "main.db"
    main_conn = nm_db.connect(main)
    before = main_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    kb = knowledge.connect(tmp_path / "kb.db")
    knowledge.add_results(kb, [FakeResult("外部知识", "https://e.example", "摘要")],
                          embed_text=False)
    kb.close()
    after = main_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert before == after == 0
    assert not main_conn.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE '%knowledge%'").fetchall()
    main_conn.close()


def test_autoresearch_log_in_separate_db(conn, tmp_path):
    """F5 审计日志：字段完整、只在独立库、留痕可查。"""
    lid = knowledge.log_autoresearch(
        conn, entity="乌江寨", query="乌江寨 有什么好玩的",
        reason="对话里出现未知地名", hit_count=5, source_event="evt_1")
    assert lid > 0
    items = knowledge.list_autoresearch(conn)
    assert len(items) == 1
    row = items[0]
    assert row["entity"] == "乌江寨"
    assert row["reason"] == "对话里出现未知地名"
    assert row["hit_count"] == 5
    assert row["status"] == "ok"
    assert row["source_event"] == "evt_1"
    assert row["created_at"]

    # 主库里没有这张表（物理隔离）
    main = nm_db.connect(tmp_path / "main.db")
    names = [r[0] for r in main.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    assert "autoresearch_log" not in names
    main.close()


def test_autoresearch_count_and_last_at(conn):
    from datetime import datetime, timedelta, timezone
    knowledge.log_autoresearch(conn, entity="A", hit_count=1)
    knowledge.log_autoresearch(conn, entity="B", hit_count=2)
    # 全在"很久以前"之后 → 计数应为 2
    old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert knowledge.autoresearch_count_since(conn, old) == 2
    # 在未来时间点之后 → 0
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert knowledge.autoresearch_count_since(conn, future) == 0
    assert knowledge.autoresearch_last_at(conn)  # 非空
