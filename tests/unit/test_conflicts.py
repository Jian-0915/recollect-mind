"""记忆冲突三件套：取代资格、画像脏值、目标误判、回答层标注。"""
import pytest

from neuromem import answer, goals, persona, store

PROFILE = "小明性格开朗，喜欢打篮球，是用户的大学同学，最近在准备考研。"


def _extract(text, base_date=None):
    who = [n for n in ("妈妈", "阿哲", "用户") if n in text]
    return {"who": who, "when": "", "occurred_at": "", "where": "",
            "affect": {"valence": 0.0, "arousal": 0.0, "label": "中性"}}


def _setup(conn, monkeypatch, vecs):
    monkeypatch.setattr(store, "extract", _extract)
    monkeypatch.setattr(store.embed, "embed", lambda t, *a, **k: vecs[t])
    monkeypatch.setattr(store.persona, "extract_relations", lambda *a, **k: [])


class TestSupersede:
    def test_different_persona_not_superseded(self, conn, monkeypatch):
        vecs = {
            "妈妈身体不太好，有腰疼的老毛病": [1.0, 0.0, 0.0, 0.0],
            "用户最近喜欢上了做咖啡，买了一个手冲壶": [0.99, 0.141, 0.0, 0.0],  # cos≈0.99
        }
        _setup(conn, monkeypatch, vecs)
        old = store.remember(conn, "妈妈身体不太好，有腰疼的老毛病", speaker="用户")[0]
        store.remember(conn, "用户最近喜欢上了做咖啡，买了一个手冲壶", speaker="用户")
        assert conn.execute("SELECT superseded_by FROM events WHERE id=?",
                            (old,)).fetchone()[0] is None

    def test_correction_supersedes_same_persona(self, conn, monkeypatch):
        vecs = {
            "阿哲在银行工作": [1.0, 0.0, 0.0, 0.0],
            "阿哲其实没有在银行工作，他是在做软件开发": [0.9, 0.436, 0.0, 0.0],  # cos≈0.90
        }
        _setup(conn, monkeypatch, vecs)
        old = store.remember(conn, "阿哲在银行工作", speaker="用户")[0]
        new = store.remember(conn, "阿哲其实没有在银行工作，他是在做软件开发", speaker="用户")[0]
        assert conn.execute("SELECT superseded_by FROM events WHERE id=?",
                            (old,)).fetchone()[0] == new


class TestProfileGuard:
    def test_zero_rejected(self):
        assert persona._valid_profile("0" * 31) is False
        assert persona._valid_profile("0000.0000-0000") is False

    def test_short_or_repeated_rejected(self):
        assert persona._valid_profile("画像内容") is False
        assert persona._valid_profile("好好好好好好好好好好好好") is False

    def test_normal_ok(self):
        assert persona._valid_profile(PROFILE) is True


class TestGoals:
    def test_role_not_goal(self):
        assert goals.extract("用户要当伴郎，周末要去帮忙布置婚礼现场") == ""
        assert goals.extract("隔段时间要去做按摩") == ""

    def test_real_plan_kept(self):
        assert goals.extract("我下个月打算回老家看望奶奶") == "回老家看望奶奶"
        assert goals.extract("我要搬家") == "搬家"


class TestAnswerConflictMark:
    def test_marked_by_date(self):
        items = [
            {"gist": "阿哲最近辞了工作正在找工作", "occurred_at": "2026-11-10"},
            {"gist": "阿哲其实没有辞职，还在原公司上班", "occurred_at": "2026-11-12"},
        ]
        answer.mark_conflicts(items)
        assert items[0]["_conflict"] == "较早说法"
        assert items[1]["_conflict"] == "较新说法"

    def test_unrelated_not_marked(self):
        items = [
            {"gist": "妈妈身体不太好，有腰疼的老毛病", "occurred_at": "2026-11-10"},
            {"gist": "用户最近喜欢上了做咖啡", "occurred_at": "2026-11-11"},
        ]
        answer.mark_conflicts(items)
        assert "_conflict" not in items[0] and "_conflict" not in items[1]

    def test_block_shows_mark(self):
        block = answer.build_memory_block([{
            "gist": "阿哲没有辞职", "kind": "verified",
            "occurred_at": "2026-11-12", "_conflict": "较新说法",
        }])
        assert "较新说法" in block