"""记忆冲突：新说法与旧说法高度相似时，旧的标记 superseded_by，召回默认只取最新。"""
from datetime import datetime, timedelta

from neuromem import config, db, recall, store


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _setup(monkeypatch, vecs):
    monkeypatch.setattr(store, "extract", lambda t, base_date=None: {
        "who": [], "when": "", "occurred_at": "", "where": "",
        "affect": {"valence": 0.0, "arousal": 0.0, "label": "中性"},
    })

    # store 与 recall 引用的是同一个 embed 模块，只能 patch 一次：
    # 写入时按文本取向量，查询词不在表里时用 vecs["q"]
    def fake(text, *a, **k):
        return vecs.get(text, vecs["q"])

    monkeypatch.setattr(store.embed, "embed", fake)


NEAR = [1.0, 0.0, 0.0, 0.0]
ALMOST = [0.87, 0.493, 0.0, 0.0]   # cos ≈ 0.87：过 0.85 取代阈值，但不触发 0.90 去重
FAR = [0.0, 0.0, 1.0, 0.0]


def test_new_statement_supersedes_old(memdb, conn, monkeypatch):
    _setup(monkeypatch, {"小明在A公司上班": NEAR, "小明跳槽去了B公司": ALMOST,
                         "q": ALMOST})
    old = store.remember(conn, "小明在A公司上班", speaker="用户")[0]
    new = store.remember(conn, "小明跳槽去了B公司", speaker="用户")[0]
    assert old != new

    marked = conn.execute("SELECT superseded_by FROM events WHERE id=?", (old,)).fetchone()[0]
    assert marked == new, "旧说法应标记为已被新说法更新"

    items = recall.recall(conn, "小明在哪上班")
    ids = [i["id"] for i in items]
    assert new in ids, "新说法应被召回"
    assert old not in ids, "默认不返回已被更新的旧说法"

    items_old = recall.recall(conn, "小明以前在哪个公司")
    assert old in [i["id"] for i in items_old], "问「以前」时应把旧说法带出来"


def test_unrelated_statement_does_not_supersede(memdb, conn, monkeypatch):
    _setup(monkeypatch, {"小明在A公司上班": NEAR, "妈妈打电话说橘子熟了": FAR, "q": NEAR})
    a = store.remember(conn, "小明在A公司上班", speaker="用户")[0]
    store.remember(conn, "妈妈打电话说橘子熟了", speaker="用户")
    assert conn.execute("SELECT superseded_by FROM events WHERE id=?", (a,)).fetchone()[0] is None


def test_threshold_is_configurable(monkeypatch):
    assert 0.8 <= config.SUPERSEDE_SIM <= 0.98