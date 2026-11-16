"""关系 CRUD：新增 / 去重更新 / 编辑 / 校验 / 删除。"""
from neuromem import api, persona


def test_add_list_and_dedupe(memdb, conn):
    persona.ensure(conn, "小明")
    persona.ensure(conn, "小红")
    conn.commit()

    r = api.add_relation(api.RelationIn(a="小明", b="小红", type="同事", strength=0.6))
    assert r["ok"] is True and r["updated"] is False

    rows = api.relations()
    assert len(rows) == 1
    assert rows[0]["id"] == r["id"]
    assert rows[0]["since"] == ""
    assert rows[0]["type"] == "同事"

    # 反向重复：只更新强度，不新建
    r2 = api.add_relation(api.RelationIn(a="小红", b="小明", type="同事", strength=0.8))
    assert r2["updated"] is True and r2["id"] == r["id"]
    row = [x for x in api.relations() if x["id"] == r["id"]][0]
    assert abs(row["strength"] - 0.8) < 1e-6


def test_edit_relation(memdb):
    r = api.add_relation(api.RelationIn(a="小明", b="小红", type="同事", strength=0.6))
    res = api.edit_relation(r["id"], api.RelationEdit(
        type="朋友", strength=1.7, since="2026-01-01"))
    assert res["ok"] is True and res["updated"] == 1

    row = [x for x in api.relations() if x["id"] == r["id"]][0]
    assert row["type"] == "朋友"
    assert abs(row["strength"] - 1.0) < 1e-6   # 强度夹到 0~1
    assert row["since"] == "2026-01-01"


def test_invalid_relations_rejected(memdb):
    r = api.add_relation(api.RelationIn(a="小明", b="小红", type="同事"))
    assert api.add_relation(api.RelationIn(a="小明", b="小明", type="朋友"))["ok"] is False
    assert api.add_relation(api.RelationIn(a="", b="小红", type="朋友"))["ok"] is False
    assert api.edit_relation(r["id"], api.RelationEdit(type="  "))["ok"] is False


def test_relation_auto_creates_persona(memdb, conn):
    r = api.add_relation(api.RelationIn(a="王哥", b="小明", type="领导"))
    assert r["ok"] is True
    assert persona.find(conn, "王哥") is not None


def test_delete_relation(memdb):
    r = api.add_relation(api.RelationIn(a="小明", b="小红", type="同事"))
    api.add_relation(api.RelationIn(a="小明", b="阿强", type="邻居"))
    assert api.delete_relation(r["id"])["ok"] is True
    assert len(api.relations()) == 1
    assert api.delete_relation("rel_nope")["ok"] is False