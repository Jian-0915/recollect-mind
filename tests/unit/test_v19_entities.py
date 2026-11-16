"""v19 回归：实体类型 / 别名归一 / 主体推断 / 聚合层。

每条用例都对应一次真实误判，注释里写清「原 bug 长什么样」。
"""
import json

import pytest

from neuromem import aggregate, db, entity, persona, recall, store


# ---------------- 实体类型 ----------------
@pytest.mark.parametrize("name,kind", [
    ("金毛", "pet"),          # 「谁养了只金毛」被它劫持了整轮召回
    ("清明", "time"),         # 「清明回了老家扫墓」把节日建成了人
    ("我们一大家子", "group"),  # 群体不是一个人
    ("医生", "other"),        # 职业泛称
    ("周野", "person"),       # 真人不能误判，否则画像被抹
    ("刘慈欣", "person"),
])
def test_kind_of(name, kind):
    assert entity.kind_of(name) == kind


def test_pet_name_from_alias_table():
    """「团子」这种自造名只能靠别名表判：它长得和人名一模一样。"""
    assert entity.kind_of("团子") == "person"
    assert entity.kind_of("团子", pet_names={"团子"}) == "pet"


@pytest.mark.parametrize("raw,canon", [
    ("我妈", "妈妈"), ("父亲", "爸爸"), ("我堂弟", "堂弟"),
    ("我外甥", "外甥"), ("我的表弟", "表弟"), ("妻子", "老婆"),
    ("我们组", "我们组"),     # 群体词不剥前缀，剥完是「们组」
])
def test_canonicalize(raw, canon):
    assert entity.canonicalize(raw) == canon


def test_kin_follows_excludes_home():
    """「我家猫」不是第三方：把「家」当亲属词会让说话人自己的事判成别人的。"""
    assert entity.kin_follows("外甥上幼儿园")
    assert not entity.kin_follows("家猫叫团子")


# ---------------- 主体推断 ----------------
@pytest.mark.parametrize("seg,who,expect", [
    ("我外甥上幼儿园，哭了一个月", ["我外甥"], "third_party"),
    ("阿岚养了只狗叫布丁", ["阿岚"], "third_party"),
    ("他昨天辞职了", ["他"], "third_party"),
    ("你摔倒了，流血了", ["你"], "counterpart"),
    ("周野今天给我带了早餐", ["周野", "我"], "counterpart"),
    ("你妈在杭州", ["妈妈"], "counterpart"),
    ("我今天摔了一跤", ["我"], "counterpart"),
    ("我们一起去爬山", ["我们"], "shared"),
    ("我们组新来了个实习生", ["我们组"], "counterpart"),
    ("清明回了老家扫墓", ["清明"], "counterpart"),
    ("周末打算和阿岚去逛花卉市场", ["阿岚"], "counterpart"),
])
def test_infer_subject(seg, who, expect):
    assert store._infer_subject(seg, who, "用户") == expect


def test_infer_subject_explicit_wins():
    assert store._infer_subject("我养了只猫", ["我"], "用户",
                                explicit="third_party") == "third_party"


def test_infer_subject_agent_is_self():
    assert store._infer_subject("我答应每周都来", ["我"], "agent") == "self"


def test_infer_subject_skips_pet():
    """宠物名不能当第三方主体：否则生成层会用「他」指一只猫。"""
    assert store._infer_subject("团子上周绝育了", ["团子"], "用户",
                                nonperson={"团子"}) == "counterpart"


def test_replace_self_keeps_possessive():
    """「我外甥」不能写成「你外甥」——第三方的事会变成对方的事。"""
    assert store._replace_self("我外甥上幼儿园", "用户") == "我外甥上幼儿园"
    assert store._replace_self("我今天摔了一跤", "用户") == "用户今天摔了一跤"
    assert store._replace_self("我们组今天团建", "用户") == "我们组今天团建"
    assert store._replace_self("我家猫叫团子", "用户") == "我家猫叫团子"


# ---------------- 写入路径归一 ----------------
def test_resolve_persona_merges_kin(conn):
    """「我妈」「母亲」「妈妈」必须落进同一张人物卡，否则画像各记一半。"""
    ids = {store.resolve_persona(conn, n) for n in ("我妈", "母亲", "妈妈")}
    conn.commit()
    assert len(ids) == 1, ids
    row = conn.execute("SELECT canonical_name,aliases_json FROM personas").fetchone()
    assert row["canonical_name"] == "妈妈"
    alias = json.loads(row["aliases_json"])
    assert "我妈" in alias and "母亲" in alias


def test_resolve_persona_marks_kind(conn):
    store.resolve_persona(conn, "金毛")
    store.resolve_persona(conn, "清明")
    store.resolve_persona(conn, "周野")
    conn.commit()
    kinds = {r["canonical_name"]: r["kind"] for r in
             conn.execute("SELECT canonical_name,kind FROM personas")}
    assert kinds["金毛"] == "pet"
    assert kinds["清明"] == "time"
    assert kinds["周野"] == "person"


def test_persona_filter_ignores_nonperson(conn):
    """「谁养了只金毛」不能因为「金毛」是人物卡就触发人物硬过滤。"""
    for n in ("金毛", "清明", "阿岚"):
        store.resolve_persona(conn, n)
    conn.commit()
    pids = recall._query_personas(conn, "谁养了只金毛，清明见")
    names = {conn.execute("SELECT canonical_name FROM personas WHERE id=?",
                          (p,)).fetchone()["canonical_name"] for p in pids}
    assert "金毛" not in names and "清明" not in names
    assert "阿岚" not in names       # 没点名


def test_profile_queue_skips_nonperson(conn):
    """非人实体不进画像队列，否则会写出「清明最近很忙」。"""
    for name in ("清明", "阿岚"):
        pid = store.resolve_persona(conn, name)
        for i in range(4):
            eid = "evt_%s_%d" % (name, i)
            conn.execute("INSERT INTO events(id,created_at,gist,archived) "
                         "VALUES(?,?,?,0)", (eid, "2026-11-10T10:00:00", "测试 %d" % i))
            conn.execute("INSERT INTO edges(source,target,type,weight) "
                         "VALUES(?,?,'involves',1.0)", (eid, pid))
    conn.commit()
    names = [r["canonical_name"] for r in persona.pending_profiles(conn, limit=10)]
    assert "清明" not in names
    assert "阿岚" in names


# ---------------- 聚合层 ----------------
@pytest.mark.parametrize("query,mode", [
    ("我一共养过几只宠物", "count"),
    ("我都去过哪些城市", "list"),
    ("我最近在做什么", "list"),
    ("我有几个朋友", "relation_count"),
    ("我养猫了吗", None),
    ("我今天吃了什么", None),
])
def test_aggregate_detect(query, mode):
    assert aggregate.detect(query) == mode


def _seed(conn, rows):
    """rows: [(id, gist, day, tags)]"""
    from neuromem import tagging
    for eid, gist, day, tags in rows:
        conn.execute(
            "INSERT INTO events(id,created_at,occurred_at,gist,archived,subject) "
            "VALUES(?,?,?,?,0,'counterpart')",
            (eid, day + "T10:00:00", day, gist))
        for t in tags or tagging.tags_for(gist):
            conn.execute("INSERT OR IGNORE INTO event_tags(event_id,tag,source) "
                         "VALUES(?,?,'auto')", (eid, t))
    conn.commit()


def test_aggregate_counts_beyond_top_k(conn):
    """聚合层存在的意义：答案在集合里，top_k 截断必然给出残缺清单。"""
    _seed(conn, [("e%d" % i, "团子今天玩了逗猫棒第%d次" % i, "2026-11-1%d" % (i % 6), None)
                 for i in range(9)])
    conn.commit()
    roll = aggregate.build(conn, "我一共养过几只宠物")
    assert roll and roll["type"] == "rollup"
    assert roll["count"] == 9, roll["gist"]
    assert "宠物" in roll["gist"]


def test_aggregate_uses_tag_scope_not_whole_db(conn):
    """必须靠主题标签收窄：全库倒出来会把工作/饮食记忆一起算进「宠物」。"""
    _seed(conn, [("e%d" % i, "团子今天玩了逗猫棒第%d次" % i, "2026-11-12", ["宠物"])
                 for i in range(3)]
          + [("w%d" % i, "项目上线加班到很晚第%d次" % i, "2026-11-12", ["工作"])
             for i in range(6)])
    conn.commit()
    roll = aggregate.build(conn, "我一共养过几只宠物")
    assert roll["count"] == 3, roll["gist"]


def test_aggregate_appears_first_in_recall(conn):
    _seed(conn, [("e%d" % i, "我去过城市%d旅行" % i, "2026-11-12", ["旅游"])
                 for i in range(3)])
    conn.commit()
    items = recall.recall(conn, "我都去过哪些城市")
    assert items and items[0]["type"] == "rollup"


def test_aggregate_off_switch(conn, monkeypatch):
    monkeypatch.setattr(recall.config, "AGGREGATE_LAYER", False)
    _seed(conn, [("e%d" % i, "我去过城市%d旅行" % i, "2026-11-12", ["旅游"])
                 for i in range(3)])
    conn.commit()
    items = recall.recall(conn, "我都去过哪些城市")
    assert not [i for i in items if i.get("type") == "rollup"]
