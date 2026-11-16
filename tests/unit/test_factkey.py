"""结构化事实键（factkey）与取代链增强单测。

三层各测一段：
1. **抽取**：谁·什么属性·什么值，以及抽不出来时必须老实返回 None。
2. **判定**：单值属性冲突要取代，多值属性永不取代，历史陈述不覆盖当前。
3. **落库**：rebuild 只标 superseded_by、backfill 只写 fact_key 且幂等。

这三层任何一层放松，都会变成"悄悄写坏记忆"——所以用例写得比较细。
"""
import pytest

from neuromem import db, factkey, store

NAMES = ["我妈", "周野", "团子", "表哥"]


def _ext(text):
    return factkey.extract(text, names=NAMES)


# ---------------- 1. 抽取 ----------------

@pytest.mark.parametrize("text,want", [
    ("我住在北京", ("counterpart", "居住地", "北京", 1)),
    ("我搬到上海了", ("counterpart", "居住地", "上海", 1)),
    ("我家在北京", ("counterpart", "居住地", "北京", 1)),
    ("我妈住在北京", ("我妈", "居住地", "北京", 1)),
    ("我在腾讯上班", ("counterpart", "工作单位", "腾讯", 1)),
    ("我跳槽到字节了", ("counterpart", "工作单位", "字节", 1)),
    # 真实语料里最常见的在职说法："在X做Y"
    ("我在腾讯做产品经理", ("counterpart", "工作单位", "腾讯", 1)),
    ("我在字节当程序员", ("counterpart", "工作单位", "字节", 1)),
    ("我离职了", ("counterpart", "在职状态", "离职", 1)),
    ("我入职了", ("counterpart", "在职状态", "在职", 1)),
    ("我结婚了", ("counterpart", "婚姻状态", "已婚", 1)),
    ("我离婚了", ("counterpart", "婚姻状态", "单身", 1)),
    ("我今年30岁", ("counterpart", "年龄", "30", 1)),
    ("我升职成经理了", ("counterpart", "职位", "经理", 1)),
    ("我喜欢猫", ("counterpart", "喜好", "猫", 0)),
    ("我喜欢狗", ("counterpart", "喜好", "狗", 0)),
    # 量词不该留在值里；「台」是量词但「台湾」是地名，不能一起切
    ("我去了趟景德镇", ("counterpart", "行踪", "景德镇", 0)),
    ("我去了趟台湾", ("counterpart", "行踪", "台湾", 0)),
    ("我买了台笔记本", ("counterpart", "拥有物", "笔记本", 0)),
    ("我讨厌香菜", ("counterpart", "厌恶", "香菜", 0)),
    # 历史陈述：降级为多值，不许覆盖当前状态
    ("我小时候住在北京", ("counterpart", "居住地", "北京", 0)),
    ("我去年住在北京", ("counterpart", "居住地", "北京", 0)),
])
def test_extract_basic(text, want):
    k = _ext(text)
    assert k is not None, text
    assert (k["s"], k["a"], k["v"], k["f"]) == want


@pytest.mark.parametrize("text", [
    "今天天气不错",
    "他叫我去开会",
    "周野换工作了",
    "我不在北京住了",                      # 否定句方向不明，不建键
    "我在家做饭",                          # 「在X做Y」不能把「家」当工作单位
    "外婆最近身体不太好，在老家住院",       # 「家住」曾被误匹配成 (外婆,居住地,院)
    "嗯",
])
def test_extract_refuses_when_unsure(text):
    assert _ext(text) is None


def test_extract_returns_none_without_subject():
    # 没有已知人物、也没有第一人称 → 不知道该挂在谁头上
    assert factkey.extract("住在北京", names=NAMES) is None


# ---------------- 2. 判定 ----------------

def test_conflict_when_functional_value_changes():
    a, b = _ext("我住在北京"), _ext("我搬到上海了")
    assert factkey.conflict(b, a) is True


def test_multi_valued_never_conflicts():
    a, b = _ext("我喜欢猫"), _ext("我喜欢狗")
    assert factkey.conflict(b, a) is False
    assert factkey.conflict(a, b) is False


def test_historical_does_not_override_current():
    old, new = _ext("我小时候住在北京"), _ext("我搬到上海了")
    assert factkey.conflict(new, old) is False


def test_different_subject_never_conflicts():
    a, b = _ext("我住在北京"), _ext("我妈住在上海")
    assert factkey.conflict(b, a) is False


def test_same_value_is_repetition_not_conflict():
    a, b = _ext("我住在北京"), _ext("我住在北京市")
    assert factkey.same(b, a) is True
    assert factkey.conflict(b, a) is False


def test_loads_tolerates_garbage():
    assert factkey.loads(None) is None
    assert factkey.loads("") is None
    assert factkey.loads("not json") is None
    assert factkey.loads('{"s":"self"}') is None      # 缺属性名
    assert factkey.loads({"s": "self", "a": "居住地"})["a"] == "居住地"


# ---------------- 3. 落库 ----------------

def _mk(conn, eid, gist, key, created, occurred=None):
    conn.execute(
        "INSERT INTO events(id,gist,created_at,occurred_at,fact_key,archived) "
        "VALUES(?,?,?,?,?,0)",
        (eid, gist, created, occurred, factkey.dumps(key)))


def test_rebuild_marks_older_functional_value(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    _mk(c, "e1", "我住在北京", {"s": "self", "a": "居住地", "v": "北京", "f": 1},
        "2026-01-01T00:00:00+08:00")
    _mk(c, "e2", "我搬到上海了", {"s": "self", "a": "居住地", "v": "上海", "f": 1},
        "2026-02-01T00:00:00+08:00")
    c.commit()

    marked = store.rebuild_factkey_conflicts(c)
    assert len(marked) == 1 and marked[0]["old"] == "e1" and marked[0]["new"] == "e2"
    row = c.execute("SELECT superseded_by FROM events WHERE id='e1'").fetchone()
    assert row["superseded_by"] == "e2"
    c.close()


def test_rebuild_keeps_multi_valued_alive(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    _mk(c, "e1", "我喜欢猫", {"s": "self", "a": "喜好", "v": "猫", "f": 0},
        "2026-01-01T00:00:00+08:00")
    _mk(c, "e2", "我喜欢狗", {"s": "self", "a": "喜好", "v": "狗", "f": 0},
        "2026-02-01T00:00:00+08:00")
    c.commit()
    assert store.rebuild_factkey_conflicts(c) == []
    alive = c.execute(
        "SELECT COUNT(*) FROM events WHERE superseded_by IS NULL").fetchone()[0]
    assert alive == 2
    c.close()


def test_rebuild_dry_run_does_not_write(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    _mk(c, "e1", "我住在北京", {"s": "self", "a": "居住地", "v": "北京", "f": 1},
        "2026-01-01T00:00:00+08:00")
    _mk(c, "e2", "我搬到上海了", {"s": "self", "a": "居住地", "v": "上海", "f": 1},
        "2026-02-01T00:00:00+08:00")
    c.commit()
    marked = store.rebuild_factkey_conflicts(c, dry_run=True)
    assert len(marked) == 1
    row = c.execute("SELECT superseded_by FROM events WHERE id='e1'").fetchone()
    assert not row["superseded_by"]
    c.close()


def test_backfill_writes_only_fact_key(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    _mk(c, "e1", "我住在北京", None, "2026-01-01T00:00:00+08:00")
    c.commit()
    res = store.backfill_factkeys(c)
    assert res["filled"] == 1
    row = c.execute("SELECT gist,fact_key,superseded_by FROM events WHERE id='e1'").fetchone()
    assert row["gist"] == "我住在北京"
    assert row["superseded_by"] is None
    assert factkey.loads(row["fact_key"])["v"] == "北京"
    c.close()


def test_backfill_is_idempotent(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    _mk(c, "e1", "我住在北京", None, "2026-01-01T00:00:00+08:00")
    c.commit()
    store.backfill_factkeys(c)
    second = store.backfill_factkeys(c)
    assert second["filled"] == 0          # 已有键不重复写
    forced = store.backfill_factkeys(c, force=True)
    assert forced["filled"] == 1          # force 才重算
    c.close()


def test_backfill_leaves_unparseable_null(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    _mk(c, "e1", "今天天气不错", None, "2026-01-01T00:00:00+08:00")
    c.commit()
    res = store.backfill_factkeys(c)
    assert res["filled"] == 0 and res["skipped"] == 1
    row = c.execute("SELECT fact_key FROM events WHERE id='e1'").fetchone()
    assert row["fact_key"] is None
    c.close()


# ---------------- 4. 新旧判定用「发生时间」而非「写入时间」 ----------------
# 补录是常态：主库 203 条里 21 条 occurred_at 与 created_at 不同日，最极端差 7 个月。
# 若按写入顺序判新旧，补录一句更早的事实就会把当前值顶掉。

def test_effective_at_prefers_occurrence_over_write():
    e = store._effective_at
    assert e("2025-03-01", "2026-11-16T09:00:00+08:00")[0] == "2025-03-01"
    assert e(None, "2026-11-16T09:00:00+08:00")[0] == "2026-11-16"
    assert e("", "") == ("", "")
    # 只到日、且正好是录入当天：没有「更早」的信息，按录入时刻算，
    # 否则同一天上午说 A、下午补一句「今天…」会被误判成补录旧事。
    assert e("2026-11-16", "2026-11-16T18:00:00+08:00") == (
        "2026-11-16", "2026-11-16T18:00:00+08:00")
    # 格式混用（带时分秒 vs 只有日期）时，截到日期前缀才不会错序
    assert e("2026-02-01T00:00:00+08:00", "x") > e("2026-01-01", "x")


def test_rebuild_uses_occurrence_time_not_write_time(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    # 当前值：11-01 说的事，就发生在 11-01
    _mk(c, "cur", "我搬到上海了", {"s": "counterpart", "a": "居住地", "v": "上海", "f": 1},
        "2026-11-01T10:00:00+08:00", occurred="2026-11-01T10:00:00+08:00")
    # 补录：11-16 才说，但讲的是 2025-03 的旧状态
    _mk(c, "old", "2025年3月我搬到杭州", {"s": "counterpart", "a": "居住地", "v": "杭州", "f": 1},
        "2026-11-16T09:00:00+08:00", occurred="2025-03-01T00:00:00+08:00")
    c.commit()

    marked = store.rebuild_factkey_conflicts(c)
    # 按写入顺序会判 e2 最新 → 上海被错误作废。按发生时间才是对的。
    assert len(marked) == 1
    assert marked[0]["old"] == "old" and marked[0]["new"] == "cur"
    assert c.execute("SELECT superseded_by FROM events WHERE id='cur'").fetchone()[0] is None
    assert c.execute("SELECT superseded_by FROM events WHERE id='old'").fetchone()[0] == "cur"
    c.close()
