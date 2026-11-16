"""目标系统与受控漂移。"""
from datetime import datetime

from neuromem import db, drift, goals


def test_extract_goal():
    assert goals.extract("我下周打算去北京出差") == "去北京出差"
    assert goals.extract("准备考一个证书") == "考一个证书"
    assert goals.extract("我要吃饭") == ""
    assert goals.extract("今天下雨了") == ""


def test_add_and_dedupe(memdb, conn):
    a = goals.add(conn, "去北京出差")
    b = goals.add(conn, "去北京出差")
    assert a == b, "同一目标只登记一次"
    assert len(goals.active(conn)) == 1
    goals.complete(conn, a)
    assert goals.active(conn) == []
    assert goals.add(conn, "去北京出差") != a, "完成后可以再次登记"


def test_observe_from_memory(memdb, conn):
    gid = goals.observe(conn, "我决定每周留一天不加班", "evt_1")
    assert gid
    row = conn.execute("SELECT text, source_event FROM goals WHERE id=?", (gid,)).fetchone()
    assert row["source_event"] == "evt_1"
    assert "不加班" in row["text"]


def test_goal_drives_attention(memdb, conn):
    goals.add(conn, "准备考一个证书", "evt_goal")
    bonus = goals.match_bonus(conn, "考试准备得怎么样了")
    assert bonus.get("evt_goal", 0) > 0
    assert goals.match_bonus(conn, "今天天气不错") == {}


def _mk(conn, eid, vec, activation=1.0, salience=0.5):
    conn.execute(
        "INSERT INTO events(id,created_at,gist,embedding,salience,activation,archived,surprise) "
        "VALUES(?,?,?,?,?,?,0,0.5)",
        (eid, datetime.now().astimezone().isoformat(timespec="seconds"),
         "事件" + eid, db.pack(vec), salience, activation))


def test_drift_is_controlled(memdb, conn):
    dim = 8
    for i in range(12):
        vec = [0.0] * dim
        vec[i % dim] = 1.0
        _mk(conn, "e%02d" % i, vec, activation=1.0 - i * 0.05, salience=0.5)
    conn.commit()

    anchor_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM events ORDER BY activation DESC LIMIT 3")]
    before = {r["id"]: db.unpack(r["embedding"]) for r in conn.execute(
        "SELECT id,embedding FROM events")}

    res = drift.drift(conn, n=3, magnitude=0.01)
    assert 0 < res["changed"] <= 3, "每轮最多动 n 条"
    assert conn.execute("SELECT COUNT(*) FROM drift_log").fetchone()[0] == 1

    after = {r["id"]: db.unpack(r["embedding"]) for r in conn.execute(
        "SELECT id,embedding FROM events")}
    for aid in anchor_ids:
        assert after[aid] == before[aid], "锚点不应漂移"

    moved = [k for k in after if after[k] != before[k]]
    assert len(moved) == res["changed"]
    for k in moved:
        cos = sum(x * y for x, y in zip(before[k], after[k]))
        assert cos >= 0.99, "自相似阈值保护"