"""概念 / 人物删除：级联清理与强制解除绑定。"""
from neuromem import api, persona, store


def test_delete_concept_cascades_links(memdb, conn):
    ev = store.remember(conn, "小明在公园被狗吓到了", speaker="用户")[0]
    conn.execute("INSERT INTO concepts(id,text,abstraction_level,confidence,created_at) "
                 "VALUES('c_del','测试概念',1,0.8,'2026-11-12')")
    conn.execute("INSERT INTO concept_links(concept_id,event_id,type) "
                 "VALUES('c_del',?,'derived_from')", (ev,))
    conn.commit()

    assert len(api.concepts()) == 1
    r = api.delete_concept("c_del")
    assert r["ok"] is True
    assert api.concepts() == []
    assert conn.execute("SELECT COUNT(*) FROM concept_links").fetchone()[0] == 0
    assert api.delete_concept("c_nope")["ok"] is False


def test_delete_persona_requires_force_when_bound(memdb, conn):
    ev = store.remember(conn, "小明在公园被狗吓到了", speaker="用户")[0]
    pid = persona.find(conn, "小明")["id"]

    r = api.delete_persona(pid)
    assert r["ok"] is False and r.get("need_force") is True
    assert persona.find(conn, "小明") is not None

    api.add_relation(api.RelationIn(a="小明", b="用户", type="朋友", strength=0.5))
    r = api.delete_persona(pid, force=1)
    assert r["ok"] is True and r["unbound"] >= 1
    assert persona.find(conn, "小明") is None
    assert conn.execute("SELECT COUNT(*) FROM edges WHERE target=?", (pid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM relations WHERE a=? OR b=?",
                        (pid, pid)).fetchone()[0] == 0
    # 只解除绑定，事件本身保留
    assert conn.execute("SELECT COUNT(*) FROM events WHERE id=?", (ev,)).fetchone()[0] == 1


def test_delete_unbound_persona(memdb, conn):
    p = persona.ensure(conn, "路人甲")
    assert api.delete_persona(p["id"])["ok"] is True
    assert api.delete_persona("p_nope")["ok"] is False