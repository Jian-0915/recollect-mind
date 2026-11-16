"""惊讶信号（预测误差）：写入时算 surprise，并驱动巩固触发。

不依赖真实模型：embed 与 extract 均被替换为固定向量 / 固定抽取结果。
"""
import pytest

from datetime import datetime, timedelta

from neuromem import autoevolve, config, embed, store

VECS = {
    "在小巷遇到一只猫": [1.0, 0.0, 0.0, 0.0],
    "在小巷遇到一只狗": [0.8, 0.6, 0.0, 0.0],   # cos=0.8 → 有点意外
    "量子纠缠实验成功了": [0.0, 0.0, 1.0, 0.0],  # cos=0   → 非常意外
}


@pytest.fixture()
def fake_model(monkeypatch):
    monkeypatch.setattr(store, "extract", lambda t, base_date=None: {
        "who": [], "when": "", "occurred_at": "", "where": "",
        "affect": {"valence": 0.0, "arousal": 0.0, "label": "中性"},
    })
    monkeypatch.setattr(embed, "embed", lambda t, *a, **k: VECS[t])


def _surprise(conn, eid):
    return conn.execute("SELECT surprise FROM events WHERE id=?", (eid,)).fetchone()[0]


def test_surprise_is_one_minus_best_similarity(memdb, conn, fake_model):
    first = store.remember(conn, "在小巷遇到一只猫", speaker="用户")[0]
    assert _surprise(conn, first) == 1.0, "空库第一条视为全新"

    close = store.remember(conn, "在小巷遇到一只狗", speaker="用户")[0]
    assert abs(_surprise(conn, close) - 0.2) < 0.01, "相似事件惊讶度低"

    far = store.remember(conn, "量子纠缠实验成功了", speaker="用户")[0]
    assert _surprise(conn, far) == 1.0, "无关联事件惊讶度拉满"


def test_duplicate_does_not_create_event(memdb, conn, fake_model):
    a = store.remember(conn, "在小巷遇到一只猫", speaker="用户")[0]
    b = store.remember(conn, "在小巷遇到一只猫", speaker="用户")[0]
    assert a == b
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_surprise_accumulates_and_triggers(memdb, conn, fake_model):
    for text in VECS:
        store.remember(conn, text, speaker="用户")

    total = autoevolve.surprise_since_last()
    assert abs(total - 2.2) < 0.01
    assert autoevolve.content_triggered() is True, "2.2 ≥ 2.0 应触发巩固"


def test_threshold_respected(memdb, conn, fake_model):
    store.remember(conn, "在小巷遇到一只猫", speaker="用户")
    # 1.0 < 1.2，且条数 1 < 5
    assert autoevolve.surprise_since_last() < config.SURPRISE_TRIGGER
    assert autoevolve.content_triggered() is False

    ts = (datetime.now().astimezone() + timedelta(seconds=5)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO growth_log(ts,events,active_events,concepts,max_level,archived) "
                 "VALUES(?,2,2,0,0,0)", (ts,))
    conn.commit()
    assert autoevolve.surprise_since_last() == 0.0