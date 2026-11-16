"""关系抽取（分句 + 规则兜底）与人物画像自动生成。

背景（2026-11-13）：
- 关系抽取原先整句喂模型，长句常被判 []（"…，性格开朗，喜欢打篮球" → []）
- 画像此前只有手动按钮会生成，代谢里从不触发
"""
from neuromem import persona

PROFILE = "小明性格开朗，喜欢打篮球，是用户的大学同学，最近在准备考研。"


def _event(c, eid, gist=""):
    c.execute(
        "INSERT INTO events(id,created_at,gist,archived) VALUES(?,?,?,0)",
        (eid, "2026-11-01T10:00:00+08:00", gist),
    )


def _bind(c, eid, pid):
    c.execute(
        "INSERT INTO edges(source,target,type,weight) VALUES(?,?,?,?)",
        (eid, pid, "involves", 1.0),
    )


class TestRuleFallback:
    def test_self_relation(self, conn):
        items = persona._rule_items(conn, "小明是用户大学认识的好朋友，性格开朗", "用户")
        assert items == [{"a": "小明", "b": "用户", "t": "朋友"}]

    def test_possessive(self, conn):
        items = persona._rule_items(conn, "娶的嫂子是表哥的大学同学", "用户")
        assert items and items[0]["a"] == "嫂子"
        assert items[0]["b"] == "表哥"
        assert items[0]["t"] == "同学"

    def test_no_false_positive(self, conn):
        # "这是" 的 "这" 不是人名，不能凭空造人
        assert persona._rule_items(conn, "这是我的朋友", "用户") == []

    def test_no_glued_fragment(self, conn):
        # 老实现会把 b 抽成 "用户大学认识"
        items = persona._rule_items(conn, "小明是用户大学认识的好朋友", "用户")
        assert all(len(i["b"]) <= 4 for i in items)

    def test_split_sentences(self):
        assert persona.sentences("a，b。c；d") == ["a", "b", "c", "d"]


class TestProfiles:
    def test_pending_needs_three_events(self, conn):
        p = persona.ensure(conn, "小明")
        for i in range(2):
            _event(conn, "e%d" % i)
            _bind(conn, "e%d" % i, p["id"])
        conn.commit()
        assert persona.pending_profiles(conn, 5) == []

        _event(conn, "e2")
        _bind(conn, "e2", p["id"])
        conn.commit()
        assert [r["canonical_name"] for r in persona.pending_profiles(conn, 5)] == ["小明"]

    def test_build_writes_stamp_and_refresh_gate(self, conn, monkeypatch):
        p = persona.ensure(conn, "小明")
        for i in range(5):
            _event(conn, "e%d" % i)
            _bind(conn, "e%d" % i, p["id"])
        conn.commit()

        monkeypatch.setattr(persona.llm, "generate", lambda *a, **k: PROFILE)
        out = persona.build_profiles(conn)
        assert [x["name"] for x in out] == ["小明"]

        row = conn.execute(
            "SELECT profile, profile_at, profile_events FROM personas WHERE id=?",
            (p["id"],)).fetchone()
        assert row["profile"] == PROFILE
        assert row["profile_at"]
        assert row["profile_events"] == 5

        # 没有新增经历 → 不再刷新
        assert persona.pending_profiles(conn, 5) == []

        # 新增 5 条 → 达到刷新门槛
        for i in range(5, 10):
            _event(conn, "e%d" % i)
            _bind(conn, "e%d" % i, p["id"])
        conn.commit()
        assert [r["canonical_name"] for r in persona.pending_profiles(conn, 5)] == ["小明"]

    def test_round_limit(self, conn, monkeypatch):
        names = ["甲", "乙", "丙"]
        for n in names:
            p = persona.ensure(conn, n)
            for i in range(3):
                eid = "%s%d" % (n, i)
                _event(conn, eid)
                _bind(conn, eid, p["id"])
        conn.commit()
        monkeypatch.setattr(persona.llm, "generate", lambda *a, **k: PROFILE)
        assert len(persona.build_profiles(conn, limit=2)) == 2