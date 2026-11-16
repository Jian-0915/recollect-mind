"""P0 修复端到端：编辑重算向量 / 图谱过滤归档 / 情感接线 / 自传生成。

依赖真实模型（embedding + LLM），默认不收集：
    python -m pytest -m integration tests/unit/test_p0_fixes.py
"""
import pytest

from neuromem import api, evolve, graph, selfnarrative, store

pytestmark = pytest.mark.integration


def test_p0_fixes_end_to_end(memdb, conn):
    ids = store.remember(conn, "小明在公园被狗吓到了，他很害怕", speaker="用户")
    ids += store.remember(conn, "小明在小巷又被狗追了，更害怕了", speaker="用户")

    # P0-2 情感接线
    emo = conn.execute("SELECT valence,arousal,label,updated_at FROM emotion_state "
                       "WHERE id=1").fetchone()
    assert emo is not None and emo["updated_at"]
    assert emo["valence"] < -0.1, "负面记忆后效价应为负，实际 %s" % emo["valence"]

    # P0-4 图谱过滤归档
    ev0 = ids[0]
    conn.execute("UPDATE events SET archived=1 WHERE id=?", (ev0,))
    conn.commit()
    assert ev0 not in graph.build_graph(conn).nodes
    conn.execute("UPDATE events SET archived=0 WHERE id=?", (ev0,))
    conn.commit()
    assert ev0 in graph.build_graph(conn).nodes

    # P0-1 编辑重算向量
    ev1 = ids[1]
    before = conn.execute("SELECT embedding FROM events WHERE id=?", (ev1,)).fetchone()[0]
    r = api.edit_event(ev1, api.EventEdit(gist="小明在小巷被邻居的狗追，吓得哭了"))
    assert r["ok"] and r["warnings"], "应提示语义边已移除"
    c2 = api.conn()
    try:
        after = c2.execute("SELECT embedding FROM events WHERE id=?", (ev1,)).fetchone()[0]
        sn = c2.execute("SELECT COUNT(*) FROM edges WHERE (source=? OR target=?) "
                        "AND type='semantic'", (ev1, ev1)).fetchone()[0]
    finally:
        c2.close()
    assert before != after, "embedding 未重算"
    assert sn == 0

    # P0-3 自我叙事 + tick 返回 narrative
    assert selfnarrative.build(conn, None)
    assert "narrative" in evolve.tick(conn)