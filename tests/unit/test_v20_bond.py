"""v20 回归：关系性质（social / mentioned）+ 聚合的概念口径与关系清单。

每条用例都对应一次真实误判，注释写清「原 bug 长什么样」。
"""
import pytest

from neuromem import aggregate, entity, persona, recall, store


# ---------------- bond_of：社交对象 vs 仅被提及 ----------------
@pytest.mark.parametrize("gists,expect", [
    # 名人：只在"消费其作品"里出现 → mentioned
    (["你超爱王家卫的电影，花样年华看了五遍"], "mentioned"),
    (["你歌单里周杰伦占了大半，从小学听到现在"], "mentioned"),
    (["最近迷科幻小说，刘慈欣和特德姜都看"], "mentioned"),
    # 真人：有交往证据 → social
    (["周野今天给我带了早餐，是我最爱的酱香饼"], "social"),
    (["阿岚上周生日，你给她烤了个戚风蛋糕"], "social"),
    (["你给外婆寄了件羽绒服，怕她冬天冷"], "social"),
    (["我们组新来了个实习生叫小赵"], "social"),
    # 混合：一条作品、一条交往 → 保守判 social（误降级代价不对称）
    (["你超爱王家卫的电影", "王家卫昨天来我们公司了"], "social"),
])
def test_bond_of(gists, expect):
    assert entity.bond_of(gists) == expect


def test_bond_relation_edge_wins():
    """有 relations 边就是社交对象，不看文本——名人也会被真认识的人覆盖。"""
    assert entity.bond_of(["王家卫的电影真好看"], has_relation=True) == "social"


def test_bond_empty_is_social():
    """没有证据时保守判 social：宁可留着当社交对象，也不误删真人。"""
    assert entity.bond_of([]) == "social"


def test_bond_ignores_novel_word():
    """「小说」里的"说"不能当社交证据——之前裸「说」把刘慈欣判成了社交对象。"""
    assert entity.bond_of(["最近迷科幻小说，刘慈欣和特德姜都看"]) == "mentioned"


# ---------------- 写入路径：remember 后自动重算 ----------------
def _persona_with(conn, name, gists):
    pid = store.resolve_persona(conn, name)
    for i, g in enumerate(gists):
        eid = "evt_%s_%d" % (pid, i)
        conn.execute("INSERT INTO events(id,created_at,gist,archived) VALUES(?,?,?,0)",
                     (eid, "2026-11-10T10:00:00", g))
        conn.execute("INSERT INTO edges(source,target,type,weight) "
                     "VALUES(?,?,'involves',1.0)", (eid, pid))
    conn.commit()
    return pid


def test_rebond_marks_celebrity(conn):
    pid = _persona_with(conn, "王家卫", ["你超爱王家卫的电影，花样年华看了五遍"])
    store._rebond(conn, {pid})
    conn.commit()
    row = conn.execute("SELECT bond FROM personas WHERE id=?", (pid,)).fetchone()
    assert row["bond"] == "mentioned"


def test_rebond_keeps_friend(conn):
    pid = _persona_with(conn, "阿岚", ["阿岚上周生日，你给她烤了个戚风蛋糕"])
    store._rebond(conn, {pid})
    conn.commit()
    assert conn.execute("SELECT bond FROM personas WHERE id=?",
                        (pid,)).fetchone()["bond"] == "social"


def test_rebond_skips_nonperson(conn):
    """宠物/节日的 bond 没有意义，不该被写成 mentioned 又被别处当人读。"""
    pid = _persona_with(conn, "金毛", ["金毛今天撞你腿了"])
    store._rebond(conn, {pid})
    conn.commit()
    assert conn.execute("SELECT bond FROM personas WHERE id=?",
                        (pid,)).fetchone()["bond"] == "social"


# ---------------- 召回 / 画像必须尊重 bond ----------------
def test_persona_filter_skips_mentioned(conn):
    """问「我喜欢看什么电影」时「王家卫」不能触发人物硬过滤。

    原 bug：pids=['王家卫'] → 候选被换成字面沾边的那几条，正解被整条删掉。
    和 v19 的「金毛劫持」是同一类故障，只是这次是人。
    """
    pid = _persona_with(conn, "王家卫", ["你超爱王家卫的电影，花样年华看了五遍"])
    store._rebond(conn, {pid})
    conn.commit()
    assert recall._query_personas(conn, "我喜欢看什么电影") == []


def test_persona_filter_keeps_social_person(conn):
    pid = _persona_with(conn, "阿岚", ["阿岚上周生日，你给她烤了个戚风蛋糕"])
    store._rebond(conn, {pid})
    conn.commit()
    assert recall._query_personas(conn, "阿岚喜欢什么") == [pid]


def test_profile_queue_skips_mentioned(conn):
    """不给名人写画像：会产出「刘慈欣最近迷科幻小说」这种把作品当社交的文本。"""
    pid = _persona_with(conn, "刘慈欣", ["最近迷科幻小说，刘慈欣和特德姜都看"] * 4)
    friend = _persona_with(conn, "周野", ["周野今天给我带了早餐"] * 4)
    store._rebond(conn, {pid, friend})
    conn.commit()
    names = [r["canonical_name"] for r in persona.pending_profiles(conn, limit=10)]
    assert "刘慈欣" not in names
    assert "周野" in names


# ---------------- 聚合：关系清单 ----------------
@pytest.mark.parametrize("query,mode", [
    ("我都有哪些朋友", "relation_list"),
    ("我有几个朋友", "relation_count"),
    ("我都有哪些家人", "relation_list"),
    ("我都有哪些爱好", "list"),        # 没有关系词 → 不是关系清单
    ("我是他朋友", None),              # 陈述句不是聚合问句
])
def test_detect_relation_modes(query, mode):
    assert aggregate.detect(query) == mode


def test_relation_list_uses_relations_not_events(conn):
    """「我都有哪些朋友」要的是人名，不是「社交」标签下的一堆饭局。"""
    me = store.resolve_persona(conn, "你")
    lw = store.resolve_persona(conn, "阿岚")
    conn.execute("INSERT INTO relations(id,a,b,rel_type,strength) "
                 "VALUES('rel_1',?,?,'闺蜜',0.5)", (lw, me))
    for i in range(3):
        eid = "evt_food_%d" % i
        conn.execute("INSERT INTO events(id,created_at,gist,archived) VALUES(?,?,?,0)",
                     (eid, "2026-11-10T10:00:00", "同事聚餐第%d次" % i))
    conn.commit()
    roll = aggregate.build(conn, "我都有哪些朋友")
    assert roll and roll["aggregate_mode"] == "relation_list"
    assert "阿岚" in roll["gist"]
    assert "聚餐" not in roll["gist"]


def test_relation_list_skips_mentioned(conn):
    """名单里不能出现只是被提及的人。

    即使误抽出一条 relations 边（"我和王家卫是朋友"这种模型幻觉），
    bond=mentioned 的人也不该进「我都有哪些朋友」。
    """
    me = store.resolve_persona(conn, "你")
    wjw = _persona_with(conn, "王家卫", ["你超爱王家卫的电影"])
    store._rebond(conn, {wjw})
    conn.execute("INSERT INTO relations(id,a,b,rel_type,strength) "
                 "VALUES('rel_2',?,?,'朋友',0.5)", (wjw, me))
    conn.commit()
    assert aggregate.build(conn, "我都有哪些朋友") is None


def test_relation_list_includes_kin(conn):
    """妈妈/外婆常只有人物卡没有 relations 边，名单不能漏掉她们。"""
    store.resolve_persona(conn, "妈妈")
    store.resolve_persona(conn, "外婆")
    conn.commit()
    roll = aggregate.build(conn, "我都有哪些家人")
    assert roll and "妈妈" in roll["gist"] and "外婆" in roll["gist"]


# ---------------- 聚合：概念口径（送礼方向）----------------
def test_concept_gift_direction(conn):
    """「我送过几次礼物」：送出算，收到不算。

    原 bug：只按"寄/送"字面数，把「你妈给你寄了笋干」「大伯给你寄橘子」
    也算成你送的；不做方向判断就没有对的计数。
    """
    for i, g in enumerate([
        "你开始练毛笔字送给同事当生日礼物",
        "你给外婆寄了件羽绒服，怕她冬天冷",
        "你学篆刻，刻了方闲章送自己",
        "你妈昨天视频说给你寄了家乡的笋干",
        "你大伯在老家种橘子，每年都给你寄一箱",
    ]):
        eid = "evt_gift_%d" % i
        conn.execute("INSERT INTO events(id,created_at,gist,archived,subject) "
                     "VALUES(?,?,?,0,'counterpart')", (eid, "2026-11-10T10:00:00", g))
    conn.commit()
    roll = aggregate.build(conn, "我送过几次礼物")
    assert roll and roll["aggregate_mode"] == "count"
    assert roll["count"] == 3, roll["gist"]
    assert "至少 3 条" in roll["gist"], "口径必须写成下界，不能宣称精确次数"
    assert "笋干" not in roll["gist"] and "橘子" not in roll["gist"]


def test_concept_skips_third_party(conn):
    """别人的加班不算我的加班。"""
    for i, g in enumerate([
        "这周特别崩溃，我们的支付模块要在周五上线，你连续加班三天了",
        "我表哥说加班比我还狠",
    ]):
        eid = "evt_ot_%d" % i
        sub = "counterpart" if i == 0 else "third_party"
        conn.execute("INSERT INTO events(id,created_at,gist,archived,subject) "
                     "VALUES(?,?,?,0,?)", (eid, "2026-11-10T10:00:00", g, sub))
    conn.commit()
    roll = aggregate.build(conn, "我加过几次班")
    assert roll and roll["count"] == 1, roll["gist"]


def test_concept_gate_rejects_whole_db(conn):
    """界定不出范围时宁可不输出，也不喂「共 N 条」——N 等于全库就是噪声。"""
    for i in range(20):
        eid = "evt_noise_%d" % i
        conn.execute("INSERT INTO events(id,created_at,gist,archived) VALUES(?,?,?,0)",
                     (eid, "2026-11-10T10:00:00", "随便一件事第%d条" % i))
    conn.commit()
    assert aggregate.build(conn, "我平时有哪些爱好") is None


def test_aggregate_off_switch(conn, monkeypatch):
    monkeypatch.setattr(recall.config, "AGGREGATE_LAYER", False)
    eid = "evt_off"
    conn.execute("INSERT INTO events(id,created_at,gist,archived) VALUES(?,?,?,0)",
                 (eid, "2026-11-10T10:00:00", "你练毛笔字送给同事当生日礼物"))
    conn.commit()
    items = recall.recall(conn, "我送过几次礼物")
    assert not [i for i in items if i.get("type") == "rollup"]
