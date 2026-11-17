# -*- coding: utf-8 -*-
"""v22：人物关系里不允许把内部 id 当名字显示。

起因（2026-11-16 用户报告）：关系页有 3 条关系的一端显示成 p_xxx 内部主键。
根因是 relations 指向的人物已被合并/删除（旧版 merge 只迁 edges 不迁 relations），
而 ``list_relations`` 的兜底是 ``name_map.get(id, id)`` —— id 直接当了名字下发。
"""


def _mk(conn, name):
    from neuromem import persona
    return persona.ensure(conn, name)


def _mk_rel(conn, a, b, t, rid="rel_test1"):
    conn.execute(
        "INSERT INTO relations(id,a,b,rel_type,strength,since,until,evidence) "
        "VALUES(?,?,?,?,?,?,?,?)", (rid, a, b, t, 0.5, "", "", "test"))
    conn.commit()


# ---------------- list_relations：缺失名字不下发 id ----------------
def test_list_relations_no_id_leak(conn):
    from neuromem import persona
    you = _mk(conn, "你")
    gege = _mk(conn, "表哥")
    _mk_rel(conn, you["id"], gege["id"], "表哥", "rel_ok")
    _mk_rel(conn, you["id"], "p_deleted001", "表姐", "rel_dangling")

    rows = {r["id"]: r for r in persona.list_relations(conn)}
    ok, bad = rows["rel_ok"], rows["rel_dangling"]

    assert ok["a"] == "你" and ok["b"] == "表哥" and not ok["broken"]
    # 悬空端：名字为空、broken=True，id 只留在 *_id 里供排障
    assert bad["b"] == "" and bad["b_missing"] and bad["broken"]
    assert bad["b_id"] == "p_deleted001"
    # 正常端字段不受影响
    assert bad["a"] == "你" and not bad["a_missing"]


def test_active_relations_skips_broken(conn):
    """召回用关系上下文：悬空关系不许把 p_xxx 拼进提示词。"""
    from neuromem import persona
    you = _mk(conn, "你")
    _mk_rel(conn, you["id"], "p_deleted001", "表姐", "rel_dangling")

    rels = persona.active_relations(conn, min_strength=0.0)
    assert rels == []


# ---------------- heal：按关系词找回同名人物 ----------------
def test_heal_relinks_by_rel_type(conn):
    from neuromem import persona
    you = _mk(conn, "你")
    gege = _mk(conn, "表哥")
    _mk_rel(conn, you["id"], "p_deleted001", "表哥", "rel_fix")

    actions = persona.heal_relations(conn, dry_run=True)
    assert len(actions) == 1         # dry-run 报告计划，但不改库
    rows = {r["id"]: r for r in persona.list_relations(conn)}
    assert rows["rel_fix"]["b"] == ""   # 仍是悬空

    actions = persona.heal_relations(conn, dry_run=False)
    assert len(actions) == 1
    rid, t, old, new, name = actions[0]
    assert (rid, t, old, new, name) == ("rel_fix", "表哥", "p_deleted001",
                                        gege["id"], "表哥")
    rows = {r["id"]: r for r in persona.list_relations(conn)}
    assert rows["rel_fix"]["b"] == "表哥" and not rows["rel_fix"]["broken"]


def test_heal_refuses_self_loop(conn):
    """关系词就是另一端的名字（你—朋友→朋友）：修了会变成自环，必须跳过。"""
    from neuromem import persona
    you = _mk(conn, "你")
    friend = _mk(conn, "朋友")
    _mk_rel(conn, "p_deleted002", friend["id"], "朋友", "rel_self")

    actions = persona.heal_relations(conn, dry_run=False)
    assert actions == []
    rows = {r["id"]: r for r in persona.list_relations(conn)}
    assert rows["rel_self"]["broken"] and rows["rel_self"]["a"] == ""


def test_heal_skips_when_no_candidate(conn):
    """库里没有与关系词同名的人物：留着，交给人工。"""
    from neuromem import persona
    you = _mk(conn, "你")
    _mk_rel(conn, you["id"], "p_deleted003", "表姐", "rel_no_cand")

    assert persona.heal_relations(conn, dry_run=False) == []


def test_heal_idempotent(conn):
    from neuromem import persona
    you = _mk(conn, "你")
    _mk(conn, "表哥")
    _mk_rel(conn, you["id"], "p_deleted004", "表哥", "rel_once")
    assert len(persona.heal_relations(conn, dry_run=False)) == 1
    assert persona.heal_relations(conn, dry_run=False) == []


# ---------------- API：人物详情 + heal 接口 ----------------
def test_api_persona_detail_flags_missing(conn):
    from neuromem import persona
    import neuromem.api as api
    you = _mk(conn, "你")
    _mk_rel(conn, you["id"], "p_deleted001", "表姐", "rel_dangling")

    d = api.persona_detail(you["id"])
    assert d["ok"]
    assert d["relations"] == [{"type": "表姐", "strength": 0.5,
                               "other": "", "other_id": "p_deleted001",
                               "other_missing": True}]


def test_api_heal_endpoint(conn):
    from neuromem import persona
    import neuromem.api as api
    you = _mk(conn, "你")
    gege = _mk(conn, "表哥")
    _mk_rel(conn, you["id"], "p_deleted001", "表哥", "rel_ep1")
    _mk_rel(conn, you["id"], "p_deleted009", "表妹", "rel_ep2")   # 无同名卡

    r = api.heal_relations_ep()
    assert r["ok"]
    assert [h["id"] for h in r["healed"]] == ["rel_ep1"]
    assert r["healed"][0]["to_name"] == "表哥"
    assert [x["id"] for x in r["remaining"]] == ["rel_ep2"]
    # remaining 里名字为空时展示 id，方便人工定位
    assert r["remaining"][0]["b"] == "p_deleted009"


def test_merge_keeps_relations_intact(conn):
    """现行 merge 会迁移 relations；这个用例钉住该行为，防止悬空再现。"""
    from neuromem import persona
    you = _mk(conn, "你")
    drop = _mk(conn, "阿岚")
    keep = _mk(conn, "小岚")
    _mk_rel(conn, you["id"], drop["id"], "朋友", "rel_m")

    assert persona.merge(conn, "小岚", "阿岚")
    rows = {r["id"]: r for r in persona.list_relations(conn)}
    assert rows["rel_m"]["b"] == "小岚" and not rows["rel_m"]["broken"]
