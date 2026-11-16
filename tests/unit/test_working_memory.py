"""工作记忆（工作台）：近期活跃记忆入选、过期移出、容量上限。"""
from datetime import datetime

import pytest

from neuromem import config, recall, store, working_memory


def test_recent_events_enter_working_set(memdb, conn):
    a = store.remember(conn, "小明在公园放风筝，他笑得很开心", speaker="用户")[0]
    b = store.remember(conn, "昨天在公司加班到很晚，有点累", speaker="用户")[0]
    assert set(working_memory.recent_ids(conn)) == {a, b}


def test_old_event_drops_out(memdb, conn):
    a = store.remember(conn, "小明在公园放风筝", speaker="用户")[0]
    b = store.remember(conn, "昨天在公司加班", speaker="用户")[0]
    conn.execute("UPDATE events SET created_at=?, last_recalled_at=NULL WHERE id=?",
                 ("2020-01-01T00:00:00+08:00", a))
    conn.commit()
    wm = working_memory.recent_ids(conn)
    assert a not in wm and b in wm


def test_capacity_limit(memdb, conn):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for i in range(8):
        conn.execute("INSERT INTO events(id,created_at,gist,activation,salience,archived) "
                     "VALUES(?,?,?,?,?,0)", ("w_%d" % i, now, "补充记忆 %d" % i, 1.0, 0.5))
    conn.commit()
    assert len(working_memory.recent_ids(conn)) <= config.WM_CAPACITY
    assert 0 < config.WM_BONUS < 0.2


@pytest.mark.integration
def test_recall_marks_working(memdb, conn):
    """需要本地 embedding 服务。"""
    store.remember(conn, "小明在公园放风筝，他笑得很开心", speaker="用户")
    res = recall.recall(conn, "风筝")
    assert res
    assert all("working" in r for r in res)