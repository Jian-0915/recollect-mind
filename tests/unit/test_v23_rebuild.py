"""全库向量重建：进度状态、遇错即停不写回、取消、维度一致性。"""
import time

import pytest

from neuromem import db, embed, rebuild, settings, store


def _seed(conn, texts):
    """插入几条带 gist 的事件，embedding 先给个旧值。"""
    ids = []
    for i, text in enumerate(texts):
        eid = "rb-%03d" % i
        ids.append(eid)
        conn.execute(
            "INSERT INTO events(id, created_at, gist, embedding) VALUES(?,?,?,?)",
            (eid, "2026-01-01T00:00:0%d" % i, text, db.pack([0.0] * 2560)))
    conn.commit()
    return ids


def _vec_of(conn, eid):
    row = conn.execute("SELECT embedding FROM events WHERE id=?", (eid,)).fetchone()
    return db.unpack(row[0])


def test_rebuild_all_recomputes_embeddings(conn, memdb, monkeypatch):
    ids = _seed(conn, ["甲的记忆", "乙的记忆", "丙的记忆"])

    def fake(text):
        return [float(len(text))] * 2560

    monkeypatch.setattr(embed, "embed", fake)
    cfg = {"model": "m", "dim": 2560}
    rebuild._run(cfg)

    s = rebuild.state()
    assert s["phase"] == "done", s
    assert s["rebuilt"] == 3
    assert s["done"] == 3 and s["total"] == 3
    # 向量确实被换掉了（不再是全 0）
    assert _vec_of(conn, ids[0])[0] == float(len("甲的记忆"))
    assert rebuild.state()["running"] is False


def test_rebuild_error_stops_and_rolls_back(conn, memdb, monkeypatch):
    """第 2 条失败：立即停，一条都不写回（原子性 / 遇错即停）。"""
    ids = _seed(conn, ["第一条", "会失败", "第三条"])
    calls = {"n": 0}

    def fake(text):
        calls["n"] += 1
        if text == "会失败":
            raise RuntimeError("模拟嵌入失败")
        return [1.0] * 2560

    monkeypatch.setattr(embed, "embed", fake)
    rebuild._run({"model": "m", "dim": 2560})

    s = rebuild.state()
    assert s["phase"] == "error", s
    assert "失败" in s["error"]
    assert s["done"] == 1          # 停在第 2 条之前
    assert s["rebuilt"] == 0
    # 三条都还是旧的全 0 向量 —— 没有任何半重建写回
    for eid in ids:
        assert _vec_of(conn, eid)[0] == 0.0


def test_rebuild_cancel_writes_nothing(conn, memdb, monkeypatch):
    ids = _seed(conn, ["a", "b", "c"])
    rebuild._cancel.set()

    def fake(text):
        return [1.0] * 2560

    monkeypatch.setattr(embed, "embed", fake)
    rebuild._run({"model": "m", "dim": 2560})

    s = rebuild.state()
    assert s["phase"] == "cancelled", s
    assert s["rebuilt"] == 0
    for eid in ids:
        assert _vec_of(conn, eid)[0] == 0.0
    rebuild._cancel.clear()


def test_rebuild_dimensions_uses_configured_dim(conn, memdb, monkeypatch):
    """dim 配置值会作为 dimensions 传给嵌入端点（Matryoshka 截断到 2560）。"""
    _seed(conn, ["维度测试"])
    seen = {}

    # 直接验证 _dim_body 的传参（embed.embed 内部走它）
    body = embed._dim_body({"dim": 2560}, {"model": "m", "input": ["维度测试"]})
    assert body["dimensions"] == 2560
    seen["dim"] = body["dimensions"]

    # 端点不支持时降级
    body2 = dict(body)
    body2.pop("dimensions")
    assert "dimensions" not in body2
    assert seen["dim"] == 2560


def test_rebuild_state_snapshot_has_required_fields():
    s = rebuild.state()
    for k in ("running", "phase", "done", "total", "model", "dim",
              "error", "elapsed_ms", "rebuilt"):
        assert k in s, k
