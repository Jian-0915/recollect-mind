"""RESET 清理范围：业务表 + 派生表 + 运行时状态 + 以后新增的表。

回归背景：v11 之前 /api/reset 用手工表清单，加入版本链 / 采纳日志后补过一次，
但 event_tags、entity_alias、自增计数仍会残留。现在改成动态枚举，
这里专门插一张「未来才会有的表」验证不会再次漏清。
"""
from datetime import datetime

import pytest

from neuromem import api, graph


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _seed(conn):
    now = _now()
    conn.execute("INSERT INTO events(id,created_at,gist,activation,salience,archived) "
                 "VALUES('e1',?, '一条记忆',1.0,0.1,0)", (now,))
    conn.execute("INSERT INTO personas(id,canonical_name,aliases_json,profile) "
                 "VALUES('p1','某人','[]','')")
    conn.execute("INSERT INTO goals(id,text,status,created_at) VALUES('g1','考个证','active',?)",
                 (now,))
    conn.execute("INSERT INTO drift_log(ts,n,magnitude) VALUES(?,5,0.01)", (now,))
    conn.execute("INSERT INTO growth_log(ts,events,active_events,concepts,max_level,archived) "
                 "VALUES(?,1,1,1,1,0)", (now,))
    conn.execute("INSERT INTO concepts(id,text,abstraction_level,confidence,created_at) "
                 "VALUES('c1','一个概念',1,0.5,?)", (now,))
    conn.execute("INSERT INTO fragments(id,text,modality,salience) VALUES('f1','碎片','text',0.4)")
    conn.execute("INSERT INTO bindings(event_id,fragment_id,role,seq) VALUES('e1','f1','body',0)")
    conn.execute("INSERT INTO edges(source,target,type,weight) VALUES('e1','p1','involves',0.5)")
    conn.execute("INSERT INTO concept_links(concept_id,event_id,type) VALUES('c1','e1','instance')")
    conn.execute("INSERT INTO relations(id,a,b,rel_type) VALUES('r1','甲','乙','朋友')")
    conn.execute("INSERT INTO event_tags(event_id,tag) VALUES('e1','旅游')")
    conn.execute("INSERT INTO entity_alias(alias,target,kind) VALUES('老妈','p1','category')")

    conn.execute("CREATE TABLE IF NOT EXISTS self_narrative("
                 "id INTEGER PRIMARY KEY CHECK(id=1), text TEXT, updated_at TEXT)")
    conn.execute("INSERT OR REPLACE INTO self_narrative(id,text,updated_at) VALUES(1,'我是…',?)",
                 (now,))
    conn.execute("CREATE TABLE IF NOT EXISTS emotion_state("
                 "id INTEGER PRIMARY KEY CHECK(id=1), valence REAL DEFAULT 0, "
                 "arousal REAL DEFAULT 0, label TEXT, updated_at TEXT)")
    conn.execute("INSERT OR REPLACE INTO emotion_state(id,valence,arousal,label) "
                 "VALUES(1,-0.8,0.9,'焦虑')")
    conn.execute("CREATE TABLE IF NOT EXISTS usage_log("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, ok INTEGER)")
    conn.execute("INSERT INTO usage_log(ts,kind,ok) VALUES(?,'llm',1)", (now,))
    conn.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('models','{\"embed\":{}}')")

    # v9~v11 新增的陪伴层 / L2 采纳 / 再巩固
    conn.execute("INSERT INTO companion_state(id,open_topics,surfaced_ids,last_topic) "
                 "VALUES(1,'[\"旧话题\"]','[]','旧话题')")
    conn.execute("INSERT INTO conversation_turns(id,session_id,ts,speaker,text) "
                 "VALUES('t1','s1',?,'用户','你好')", (now,))
    conn.execute("INSERT INTO adoption_log(ts,query,source,used_ids) "
                 "VALUES(?,'在吗','agent','[\"e1\"]')", (now,))
    conn.execute("INSERT INTO memory_versions(event_id,revision,gist,created_at) "
                 "VALUES('e1',1,'旧版本',?)", (now,))

    # 模拟「以后才加的机制表」：动态枚举必须自动纳入清库
    conn.execute("CREATE TABLE IF NOT EXISTS future_mechanism_table(id TEXT, note TEXT)")
    conn.execute("INSERT INTO future_mechanism_table(id,note) VALUES('x','未来表')")
    conn.commit()


def _count(conn, table):
    try:
        return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    except Exception:
        return 0


@pytest.fixture(autouse=True)
def _no_usage_clear(monkeypatch):
    """usage.clear() 走独立长连接（config.DB_PATH），测试里不许碰真实库。"""
    monkeypatch.setattr(api.usage, "clear", lambda: None)


def test_reset_all_clears_business_and_derived(memdb, conn):
    _seed(conn)
    graph.invalidate()
    assert len(graph.build_graph(conn).nodes) >= 2      # 先让进程内图缓存有内容

    r = api.reset_all({"confirm": "RESET"})
    assert r["ok"] and r["scope"] == "all", r
    new_tables = ("memory_versions", "adoption_log", "conversation_turns",
                  "companion_state", "event_tags", "entity_alias",
                  "future_mechanism_table", "usage_log", "emotion_state")
    for t in new_tables + ("events", "personas", "goals", "drift_log",
                           "self_narrative", "concepts", "relations", "edges",
                           "fragments", "bindings", "growth_log"):
        assert t in r["cleared"], "表 %s 不在清库清单" % t
    for t in ("events", "personas", "goals", "drift_log", "self_narrative",
              "concepts", "relations", "edges", "fragments", "bindings",
              "growth_log", "event_tags", "entity_alias", "usage_log",
              "companion_state", "conversation_turns", "adoption_log",
              "memory_versions", "future_mechanism_table"):
        assert _count(conn, t) == 0, "表 %s 未清空" % t

    emo = conn.execute("SELECT valence,arousal,label FROM emotion_state WHERE id=1").fetchone()
    assert emo["valence"] == 0 and emo["arousal"] == 0 and emo["label"] == "平静"
    assert _count(conn, "sqlite_sequence") == 0, "自增计数未归零"
    assert "settings" in r["kept"] and _count(conn, "settings") == 1, "模型配置不应被清掉"
    assert r["graph_invalidated"] is True
    assert len(graph.build_graph(conn).nodes) == 0, "清库后图缓存仍带旧节点"


def test_reset_derived_keeps_memories_and_l2(memdb, conn):
    _seed(conn)
    r = api.reset_all({"confirm": "RESET", "scope": "derived"})
    assert r["ok"] and r["scope"] == "derived"
    assert _count(conn, "events") == 1, "派生清理不应删记忆"
    assert _count(conn, "personas") == 1
    assert _count(conn, "adoption_log") == 1, "派生清理不应删 L2 采纳记录"
    assert _count(conn, "memory_versions") == 1
    assert _count(conn, "conversation_turns") == 1
    assert _count(conn, "future_mechanism_table") == 1
    assert _count(conn, "goals") == 0
    assert _count(conn, "drift_log") == 0
    assert _count(conn, "self_narrative") == 0
    assert "usage_cleared" not in r


def test_reset_requires_confirm(memdb, conn):
    assert api.reset_all({})["ok"] is False
    assert api.reset_all({"confirm": "reset"})["ok"] is False
    assert api.reset_all({"confirm": "RESET", "scope": "xxx"})["ok"] is False


def test_reset_plan_is_readonly(memdb, conn):
    _seed(conn)
    plan = api.reset_plan("all")
    assert plan["ok"] and plan["scope"] == "all"
    assert "memory_versions" in plan["will_clear"]
    assert "future_mechanism_table" in plan["will_clear"]
    assert "settings" in plan["will_keep"]
    assert plan["rows"]["events"] == 1
    assert _count(conn, "events") == 1, "预检不能改数据"
    assert api.reset_plan("bad")["ok"] is False


def test_export_covers_new_tables_without_vectors(memdb, conn):
    _seed(conn)
    data = api.export_all()
    for t in ("events", "companion_state", "conversation_turns",
              "adoption_log", "memory_versions", "event_tags", "entity_alias"):
        assert t in data, "导出缺少 %s" % t
    assert "embedding" not in (data["memory_versions"][0] if data["memory_versions"] else {})
    assert "embedding" not in (data["events"][0] if data["events"] else {})
