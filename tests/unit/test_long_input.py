"""长句处理：细切、主语继承、时间上下文、片段去重。"""
from datetime import datetime

import pytest

from neuromem import store, timeparse

LONG = ("小明是用户大学认识的好朋友，性格开朗，喜欢打篮球，最近在准备考研，"
        "每天早上六点就起来背单词，周末还去图书馆自习")


def _vec(text):
    v = [0.0] * 8
    for i, ch in enumerate(text):
        v[i % 8] += (ord(ch) % 17) / 17.0
    return v


@pytest.fixture()
def offline(monkeypatch):
    """不连模型的写入环境：固定向量 + 固定抽取结果。"""
    monkeypatch.setattr(store.embed, "embed", lambda t, *a, **k: _vec(t))
    monkeypatch.setattr(store.persona, "extract_relations", lambda *a, **k: [])

    def _extract(text, base_date=None):
        who = ["小明"] if "小明" in text else []
        return {"who": who, "when": "", "occurred_at": base_date or "",
                "where": "", "affect": {"valence": 0.0, "arousal": 0.0, "label": "中性"}}

    monkeypatch.setattr(store, "extract", _extract)


class TestSegment:
    def test_long_sentence_split(self):
        segs = store.segment(LONG)
        # F4（2026-11-15）：主语切换 / 并列新事实强制细切，避免多个独立事实
        # 被拼成一条巨型 gist。旧契约「3 段、每段≥12 字」随细切规则作废。
        assert len(segs) == 5
        assert segs[0] == "小明是用户大学认识的好朋友，性格开朗"
        assert all(len(s) >= 4 for s in segs)
        assert any("考研" in s for s in segs) and any("图书馆" in s for s in segs)

    def test_short_sentences_kept(self):
        assert store.segment("小明今天去公园跑步。妈妈做了红烧肉。") == [
            "小明今天去公园跑步", "妈妈做了红烧肉"]

    def test_tail_merged(self):
        segs = store.segment(LONG)
        assert "图书馆" in segs[-1]  # 9 字尾巴并回上一段，不单独成事件


class TestTimeContext:
    def test_next_day(self):
        assert timeparse.parse_date("第二天早上", ctx=datetime(2026, 11, 4)) == "2026-11-05"

    def test_next_day_across_year(self):
        assert timeparse.parse_date("次日", ctx=datetime(2026, 12, 31)) == "2027-01-01"

    def test_relative_still_uses_base(self):
        assert timeparse.parse_date("昨天", base=datetime(2026, 11, 12)) == "2026-11-11"


class TestRemember:
    def test_subject_carried(self, conn, offline):
        ids = store.remember(conn, LONG, speaker="用户")
        assert len(ids) == 5
        for eid in dict.fromkeys(ids[1:]):
            who = [r["canonical_name"] for r in conn.execute(
                "SELECT p.canonical_name FROM edges e JOIN personas p ON p.id=e.target "
                "WHERE e.source=? AND e.type='involves'", (eid,))]
            assert "小明" in who  # 后两段没写主语，必须继承

    def test_speaker_bound(self, conn, offline):
        ids = store.remember(conn, "小明是用户大学认识的好朋友", speaker="用户")
        who = [r["canonical_name"] for r in conn.execute(
            "SELECT p.canonical_name FROM edges e JOIN personas p ON p.id=e.target "
            "WHERE e.source=? AND e.type='involves'", (ids[0],))]
        assert "用户" in who and "小明" in who

    def test_fragment_is_not_duplicate(self, conn, offline):
        a = store.remember(conn, "小明是用户大学认识的好朋友，性格开朗，喜欢打篮球", speaker="用户")
        b = store.remember(conn, "小明是用户大学认识的好朋友", speaker="用户")
        assert a == b  # 子串 → 合并到长句
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1

    def test_new_info_not_swallowed(self, conn, offline, monkeypatch):
        monkeypatch.setattr(store.embed, "embed",
                            lambda t, *a, **k: [1.0, 0.0] if "好朋友" in t else [0.0, 1.0])
        store.remember(conn, "小明是用户大学认识的好朋友", speaker="用户")
        store.remember(conn, "小明最近在准备考研，每天早上六点起来背单词", speaker="用户")
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2