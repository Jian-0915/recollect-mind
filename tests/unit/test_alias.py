"""实体链接：类别词 ↔ 名字（"猫" ↔ "团子"），以及查询时的关联召回。"""
import pytest

from neuromem import alias, query, recall, store

V = [1.0, 0.0, 0.0, 0.0]


def _extract(text, base_date=None):
    who = ["团子"] if "团子" in text else []
    return {"who": who, "when": "", "occurred_at": "", "where": "",
            "affect": {"valence": 0.0, "arousal": 0.0, "label": "中性"}}


@pytest.fixture()
def offline(monkeypatch):
    monkeypatch.setattr(store, "extract", _extract)
    monkeypatch.setattr(store.persona, "extract_relations", lambda *a, **k: [])


class TestLearn:
    def test_learn_category(self, conn):
        assert alias.learn(conn, "用户养了一只猫，名字叫团子，是去年抱回来的") == [("猫", "团子")]
        conn.commit()
        assert alias.targets(conn, "我的猫怕什么") == ["团子"]

    def test_reverse(self, conn):
        assert alias.learn(conn, "团子是一只猫，胆子很小") == [("猫", "团子")]

    def test_no_self_alias(self, conn):
        assert alias.learn(conn, "猫叫猫") == []
        assert alias.learn(conn, "今天去公园看猫") == []

    def test_query_without_alias(self, conn):
        alias.learn(conn, "用户养了一只猫，名字叫团子")
        assert alias.targets(conn, "阿哲最近怎么样") == []


class TestManual:
    def test_add_and_remove(self, conn):
        assert alias.add(conn, "主子", "团子") is True
        assert alias.targets(conn, "主子怕什么") == ["团子"]
        assert alias.add(conn, "主子", "团子") is False      # 重复不报错
        assert alias.remove(conn, "主子", "团子") is True
        assert alias.targets(conn, "主子怕什么") == []

    def test_reject_self_alias(self, conn):
        assert alias.add(conn, "猫", "猫") is False


class TestRecallByAlias:
    def test_cat_question_hits_named_cat(self, conn, offline, monkeypatch):
        vecs = {
            "用户养了一只猫，名字叫团子": [1.0, 0.0, 0.0, 0.0],
            "团子特别怕吹风机，一开就钻到沙发底下": [0.5, 0.866, 0.0, 0.0],
            "用户每周交一次线上摄影课作业": [0.0, 0.0, 1.0, 0.0],
        }
        monkeypatch.setattr(store.embed, "embed", lambda t, *a, **k: vecs[t])
        store.remember(conn, "用户养了一只猫，名字叫团子", speaker="用户")
        store.remember(conn, "团子特别怕吹风机，一开就钻到沙发底下", speaker="用户")
        store.remember(conn, "用户每周交一次线上摄影课作业", speaker="用户")
        conn.commit()

        q = "我的猫怕什么"
        items = recall.recall(conn, q, top_k=8)
        gists = [i["gist"] for i in items]
        assert any("吹风机" in g for g in gists)
        # 不相关的记忆应被焦点词压下去
        assert not any("摄影课" in g for g in gists)

    def test_query_text_not_rewritten(self, conn, offline, monkeypatch):
        vecs = {"用户养了一只猫，名字叫团子": [1.0, 0.0, 0.0, 0.0],
                "团子特别怕吹风机": [0.5, 0.866, 0.0, 0.0]}
        monkeypatch.setattr(store.embed, "embed", lambda t, *a, **k: vecs[t])
        store.remember(conn, "用户养了一只猫，名字叫团子", speaker="用户")
        store.remember(conn, "团子特别怕吹风机", speaker="用户")
        conn.commit()
        r = query.query(conn, "我的猫怕什么", with_summary=False)
        assert r["query"] == "我的猫怕什么"