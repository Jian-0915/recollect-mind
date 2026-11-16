"""问题清单（2026-11-13）回归：A1 / B2 / C1 / C2 / C4 / C6。"""
from datetime import datetime

import pytest

from neuromem import answer, db, persona, recall, store, timeparse

V = [1.0, 0.0, 0.0, 0.0]


def _event(c, eid, gist, created="2026-11-01T10:00:00+08:00"):
    c.execute(
        "INSERT INTO events(id,created_at,gist,embedding,archived,confidence) "
        "VALUES(?,?,?,?,0,0.9)", (eid, created, gist, db.pack(V)))


def _concept(c, cid, text, created="2026-01-01T10:00:00+08:00"):
    c.execute(
        "INSERT INTO concepts(id,text,abstraction_level,confidence,created_at,embedding) "
        "VALUES(?,?,?,?,?,?)", (cid, text, 1, 0.9, created, db.pack(V)))


class TestConceptQuota:
    def test_concept_scaled_and_capped(self, conn, monkeypatch):
        monkeypatch.setattr(recall.embed, "embed", lambda q, *a, **k: V)
        _event(conn, "e1", "猫会自己开门")
        for i in range(5):
            _concept(conn, "c%d" % i, "猫相关概念 %d" % i)
        conn.commit()

        items = recall.recall(conn, "猫", top_k=10)
        cons = [i for i in items if i["type"] == "concept"]
        assert len(cons) <= 2
        assert all(i["score"] <= 0.45 for i in cons)


class TestAnswerGuard:
    def test_short_answer_allowed(self):
        assert answer._valid_answer("团子") is True
        assert answer._valid_answer("红烧肉") is True

    def test_degenerate_rejected(self):
        assert answer._valid_answer("0000000") is False
        assert answer._valid_answer("。。。。。") is False


class TestCorrectionChain:
    def test_rebuild_marks_chain(self, conn, monkeypatch):
        vecs = {
            "阿哲在银行工作": [1.0, 0.0, 0.0, 0.0],
            "阿哲其实没有在银行工作": [0.9, 0.436, 0.0, 0.0],
            "阿哲其实在银行上班，之前说做软件开发是记错了": [0.6, 0.8, 0.0, 0.0],
        }
        monkeypatch.setattr(store.embed, "embed", lambda t, *a, **k: vecs[t])
        monkeypatch.setattr(store, "extract", lambda t, base_date=None: {
            "who": ["阿哲"], "when": "", "occurred_at": "", "where": "",
            "affect": {"valence": 0.0, "arousal": 0.0, "label": "中性"}})
        monkeypatch.setattr(store.persona, "extract_relations", lambda *a, **k: [])

        a = store.remember(conn, "阿哲在银行工作", speaker="用户")[0]
        b = store.remember(conn, "阿哲其实没有在银行工作", speaker="用户")[0]
        c = store.remember(conn, "阿哲其实在银行上班，之前说做软件开发是记错了", speaker="用户")[0]

        assert store.rebuild_conflicts(conn) >= 0
        rows = {r["id"]: r["superseded_by"] for r in conn.execute(
            "SELECT id,superseded_by FROM events WHERE id IN (?,?,?)", (a, b, c))}
        assert rows[a] is not None          # 最老的被取代
        assert rows[c] is None              # 最新说法保持活跃

    def test_unrelated_not_marked(self, conn, monkeypatch):
        monkeypatch.setattr(store.embed, "embed", lambda t, *a, **k: V)
        monkeypatch.setattr(store, "extract", lambda t, base_date=None: {
            "who": ["阿哲"], "when": "", "occurred_at": "", "where": "",
            "affect": {"valence": 0.0, "arousal": 0.0, "label": "中性"}})
        monkeypatch.setattr(store.persona, "extract_relations", lambda *a, **k: [])
        store.remember(conn, "阿哲喜欢钓鱼", speaker="用户")
        store.remember(conn, "阿哲周末去水库钓鱼", speaker="用户")
        conn.execute("UPDATE events SET superseded_by=NULL")
        conn.commit()
        store.rebuild_conflicts(conn)
        assert conn.execute(
            "SELECT COUNT(*) FROM events WHERE superseded_by IS NOT NULL").fetchone()[0] == 0


class TestConfidence:
    def test_hedged_text_lower_confidence(self, conn, monkeypatch):
        monkeypatch.setattr(store.embed, "embed", lambda t, *a, **k: V)
        monkeypatch.setattr(store, "extract", lambda t, base_date=None: {
            "who": ["妈妈"], "when": "", "occurred_at": "", "where": "",
            "affect": {"valence": 0.0, "arousal": 0.0, "label": "中性"}})
        monkeypatch.setattr(store.persona, "extract_relations", lambda *a, **k: [])
        eid = store.remember(conn, "听说妈妈可能要去外地", speaker="用户")[0]
        assert conn.execute("SELECT confidence FROM events WHERE id=?",
                            (eid,)).fetchone()[0] < 0.9


class TestGenericPersona:
    def test_generic_not_created(self, conn):
        persona.extract_relations(conn, "用户和朋友去看露天电影", speaker="用户")
        assert persona.find(conn, "朋友") is None


class TestRelativeTime:
    def test_months_and_years(self):
        base = datetime(2026, 11, 13)
        assert timeparse.parse_date("三个月前", base=base) == "2026-08-13"
        assert timeparse.parse_date("去年", base=base) == "2025-11-13"
        assert timeparse.parse_date("前年", base=base) == "2024-11-13"

    def test_english(self):
        base = datetime(2026, 11, 13)
        assert timeparse.parse_date("next week", base=base) == "2026-11-20"
        assert timeparse.parse_date("last month", base=base) == "2026-10-13"
        assert timeparse.parse_date("yesterday", base=base) == "2026-11-12"


class TestInvalidInput:
    def test_symbol_only_not_stored(self, conn, monkeypatch):
        monkeypatch.setattr(store.embed, "embed", lambda t, *a, **k: V)
        assert store.remember(conn, "！！！@@@", speaker="用户") == []
        assert store.LAST_WRITE["new"] == 0