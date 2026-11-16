"""记忆标签：规则打标、批量查询、回溯。"""
from neuromem import db, store, tagging


class TestTagsFor:
    def test_travel(self):
        assert "旅游" in tagging.tags_for("周末去杭州西湖玩了两天，住了民宿")

    def test_work_and_family(self):
        tags = tagging.tags_for("表哥下周结婚，用户要请假回老家", limit=3)
        assert "家庭" in tags
        assert len(tags) <= 3

    def test_pet(self):
        assert tagging.tags_for("家里的猫团子学会自己开门") == ["宠物"]

    def test_no_hit(self):
        assert tagging.tags_for("今天天气不错") == []


class TestStorage:
    def test_add_and_query(self, conn):
        conn.execute("INSERT INTO events(id,created_at,gist) VALUES('e1','2026-11-01','x')")
        tagging.add(conn, "e1", ["旅游", "摄影"], "user")
        tagging.add(conn, "e1", ["旅游"])            # 去重
        conn.commit()
        assert tagging.for_event(conn, "e1") == ["旅游", "摄影"]
        assert {t["tag"]: t["count"] for t in tagging.all_tags(conn)}["旅游"] == 1

    def test_by_events_batch(self, conn):
        for eid, tag in (("a", "旅游"), ("b", "工作"), ("b", "家庭")):
            conn.execute("INSERT OR IGNORE INTO events(id,created_at,gist) VALUES(?,'t',?)",
                         (eid, eid))
            tagging.add(conn, eid, [tag])
        conn.commit()
        m = tagging.by_events(conn, ["a", "b", "c"])
        assert m["a"] == ["旅游"]
        assert set(m["b"]) == {"工作", "家庭"}
        assert m["c"] == []


class TestWriteAndBackfill:
    def test_remember_tags_auto(self, conn, monkeypatch):
        monkeypatch.setattr(store.embed, "embed", lambda t, *a, **k: [1.0, 0.0])
        monkeypatch.setattr(store, "extract", lambda t, base_date=None: {
            "who": [], "when": "", "occurred_at": "", "where": "",
            "affect": {"valence": 0.0, "arousal": 0.0, "label": "中性"}})
        monkeypatch.setattr(store.persona, "extract_relations", lambda *a, **k: [])
        eid = store.remember(conn, "周末去丽江旅行，住了民宿", speaker="用户")[0]
        assert "旅游" in tagging.for_event(conn, eid)

    def test_custom_tag_kept(self, conn, monkeypatch):
        monkeypatch.setattr(store.embed, "embed", lambda t, *a, **k: [1.0, 0.0])
        monkeypatch.setattr(store, "extract", lambda t, base_date=None: {
            "who": [], "when": "", "occurred_at": "", "where": "",
            "affect": {"valence": 0.0, "arousal": 0.0, "label": "中性"}})
        monkeypatch.setattr(store.persona, "extract_relations", lambda *a, **k: [])
        eid = store.remember(conn, "整理了一份摄影作品集", speaker="用户", tags=["摄影"])[0]
        assert "摄影" in tagging.for_event(conn, eid)

    def test_backfill(self, conn):
        conn.execute("INSERT INTO events(id,created_at,gist,archived) "
                     "VALUES('old','2026-11-01','去成都吃火锅',0)")
        conn.commit()
        assert tagging.backfill(conn) == 1
        assert "旅游" in tagging.for_event(conn, "old")
        assert tagging.backfill(conn) == 0      # 幂等