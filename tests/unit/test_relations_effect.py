"""P0-7 / P0-8：关系强度与时间边界真正参与召回与回答；合并保留画像。

不依赖模型：事件与向量直接落库。
"""
from datetime import datetime

from neuromem import answer, db, persona, query, recall


def _mk_event(conn, eid, gist, vec, pid):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO events(id,created_at,gist,embedding,salience,activation,archived,surprise) "
        "VALUES(?,?,?,?,?,1.0,0,0.5)", (eid, now, gist, db.pack(vec), 0.5))
    conn.execute("INSERT INTO edges(source,target,type,weight) VALUES(?,?,?,1.0)",
                 (eid, pid, "involves"))


def _setup(memdb, conn):
    a = persona.ensure(conn, "小明")
    b = persona.ensure(conn, "小红")
    _mk_event(conn, "e_a", "小明今天在图书馆看书", [1.0, 0.0, 0.0, 0.0], a["id"])
    _mk_event(conn, "e_b", "小红在公司加班到深夜", [0.0, 1.0, 0.0, 0.0], b["id"])
    conn.commit()
    return a, b


def test_active_relations_filters_ended_and_weak(memdb, conn):
    a, b = _setup(memdb, conn)
    conn.execute("INSERT INTO relations(id,a,b,rel_type,strength,since,until,evidence) "
                 "VALUES('r1',?,?,'朋友',0.9,'','','x')", (a["id"], b["id"]))
    conn.execute("INSERT INTO relations(id,a,b,rel_type,strength,since,until,evidence) "
                 "VALUES('r2',?,?,'同事',0.1,'','','x')", (a["id"], b["id"]))
    conn.execute("INSERT INTO relations(id,a,b,rel_type,strength,since,until,evidence) "
                 "VALUES('r3',?,?,'前同事',0.9,'2019-01-01','2020-01-01','x')",
                 (a["id"], b["id"]))
    conn.commit()

    rels = persona.active_relations(conn)
    assert [r["id"] for r in rels] == ["r1"], "低强度与已结束的关系都应被排除"
    assert rels[0]["a"] == "小明" and rels[0]["b"] == "小红"


def test_recall_expands_through_relation(memdb, conn, monkeypatch):
    a, b = _setup(memdb, conn)
    conn.execute("INSERT INTO relations(id,a,b,rel_type,strength,since,until,evidence) "
                 "VALUES('r1',?,?,'朋友',1.0,'','','x')", (a["id"], b["id"]))
    conn.commit()
    monkeypatch.setattr(recall.embed, "embed", lambda q, *args, **k: [0.7, 0.714, 0.0, 0.0])

    items = recall.recall(conn, "小明在做什么")
    gists = [i["gist"] for i in items]
    assert any("图书馆" in g for g in gists)
    assert any("加班" in g for g in gists), "应顺着「小明—朋友—小红」把小红的事件带出来"


def test_recent_without_occurred_at_still_recallable(memdb, conn, monkeypatch):
    """「最近怎么样」带出时间范围时，无 occurred_at 但刚写入的记忆不该被误杀。"""
    a, _ = _setup(memdb, conn)
    monkeypatch.setattr(recall.embed, "embed", lambda q, *args, **k: [1.0, 0.0, 0.0, 0.0])
    items = recall.recall(conn, "小明最近怎么样")
    assert any("图书馆" in i["gist"] for i in items)


def test_ended_relation_does_not_expand(memdb, conn, monkeypatch):
    a, b = _setup(memdb, conn)
    conn.execute("INSERT INTO relations(id,a,b,rel_type,strength,since,until,evidence) "
                 "VALUES('r1',?,?,'朋友',1.0,'2019-01-01','2020-01-01','x')",
                 (a["id"], b["id"]))
    conn.commit()
    monkeypatch.setattr(recall.embed, "embed", lambda q, *args, **k: [0.7, 0.714, 0.0, 0.0])

    items = recall.recall(conn, "小明最近在做什么")
    assert not any("加班" in i["gist"] for i in items), "已结束的关系不应再牵连记忆"


def test_query_returns_relation_context(memdb, conn):
    a, b = _setup(memdb, conn)
    conn.execute("INSERT INTO relations(id,a,b,rel_type,strength,since,until,evidence) "
                 "VALUES('r1',?,?,'朋友',0.9,'','','x')", (a["id"], b["id"]))
    conn.commit()
    r = query.query(conn, "小明和小红是什么关系", with_summary=False)
    assert r["relations"] and r["relations"][0]["type"] == "朋友"


def test_answer_prompt_includes_relation_block():
    block = answer.build_relation_block([
        {"a": "小明", "b": "小红", "type": "朋友", "strength": 0.9,
         "since": "2026-01-01", "until": ""},
    ])
    assert "朋友" in block and "0.90" in block and "2026-01-01" in block
    assert answer.build_relation_block([]) == "（无）"


def test_merge_keeps_both_profiles(memdb, conn):
    k = persona.ensure(conn, "小明")
    d = persona.ensure(conn, "明明")
    conn.execute("UPDATE personas SET profile='小明是同事，性格稳重' WHERE id=?", (k["id"],))
    conn.execute("UPDATE personas SET profile='明明喜欢钓鱼，周末常去水库' WHERE id=?", (d["id"],))
    conn.commit()

    assert persona.merge(conn, "小明", "明明") is True
    p = persona.find(conn, "小明")
    assert "性格稳重" in p["profile"] and "钓鱼" in p["profile"], "画像不应随合并丢失"