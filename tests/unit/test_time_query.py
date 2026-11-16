"""时间查询：问「昨天」只返回昨天的记忆，回答块里带绝对日期。"""
from datetime import datetime, timedelta

from neuromem import answer, db, recall


def _day(offset):
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _mk(conn, eid, gist, occ, vec):
    conn.execute(
        "INSERT INTO events(id,created_at,occurred_at,gist,embedding,salience,activation,"
        "archived,surprise) VALUES(?,?,?,?,?,0.5,1.0,0,0.5)",
        (eid, occ + "T10:00:00+08:00", occ, gist, db.pack(vec)))


def test_yesterday_only_returns_yesterday(memdb, conn, monkeypatch):
    yesterday, today = _day(-1), _day(0)
    _mk(conn, "e_y", "搭建了神经记忆系统", yesterday, [0.0, 1.0, 0.0, 0.0])
    _mk(conn, "e_t", "今天在公园跑步", today, [1.0, 0.0, 0.0, 0.0])
    conn.commit()

    # 故意让"昨天"那条与问题的向量相似度为 0：靠时间窗保底命中
    monkeypatch.setattr(recall.embed, "embed", lambda q, *a, **k: [1.0, 0.0, 0.0, 0.0])

    items = recall.recall(conn, "我昨天做了什么")
    gists = [i["gist"] for i in items]
    assert any("神经记忆系统" in g for g in gists), "昨天的事件应被召回"
    assert not any("公园跑步" in g for g in gists), "今天的记忆不该出现在「昨天」的结果里"


def test_answer_block_uses_absolute_date(memdb, conn):
    yesterday = _day(-1)
    _mk(conn, "e1", "搭建了神经记忆系统", yesterday, [0.0, 1.0, 0.0, 0.0])
    conn.commit()
    items = recall.recall(conn, "昨天做了什么") or [{
        "id": "e1", "gist": "搭建了神经记忆系统", "kind": "verified",
        "occurred_at": yesterday, "time_text": "今天", "place": "", "affect": {},
    }]
    block = answer.build_memory_block(items)
    assert yesterday in block, "记忆块必须带发生日期"
    assert "（今天）" not in block, "原文相对时间不应作为唯一时间信息"


def test_empty_result_wording_for_time_question():
    assert "那段时间" in answer.answer("昨天做了什么", [])
    assert "还没有相关记忆" in answer.answer("我喜欢什么", [])

# ---------------- 2026-11-16 排序压平回归 ----------------
def test_recall_scores_keep_spread_inside_time_window(memdb, conn, monkeypatch):
    """时间窗保底不能把窗内所有记忆压成同一个分。

    实测 bug：`s = max(s, 0.42) + 0.12` 让「我最近在做什么」12 条候选里
    8 条精确等于 0.5922（base 0.1489~0.5341 被抹平），排序退化成数据库
    顺序——问「最近的项目」答不出，就是这里来的。
    """
    from datetime import datetime
    occ = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    # 三条都在窗内，向量相似度依次递减
    _mk(conn, "e_hi", "高相关的那件事", occ, [1.0, 0.0, 0.0, 0.0])
    _mk(conn, "e_mid", "中等相关的那件事", occ, [0.6, 0.8, 0.0, 0.0])
    _mk(conn, "e_lo", "低相关的那件事", occ, [0.0, 0.0, 1.0, 0.0])
    conn.commit()
    monkeypatch.setattr(recall.embed, "embed", lambda q, *a, **k: [1.0, 0.0, 0.0, 0.0])

    items = recall.recall(conn, "我最近做了什么")
    # v19：聚合层会给「最近做了什么」追加一条统计候选（全量时间线），
    # 它固定排在最前。这里要验的是**事件之间**的语义排序没被窗内保底压平，
    # 所以把统计项排除后再看。
    events = [i for i in items if i.get("type") != "rollup"]
    scores = [round(float(i["score"]), 4) for i in events]
    assert len(scores) == len(set(scores)), "窗内候选不能同分：%s" % scores
    gists = [i["gist"] for i in events]
    assert gists[0] == "高相关的那件事", "语义最相关的那条必须排第一：%s" % gists
    # 聚合统计要么不出现（3 条池子全命中 → 覆盖率闸门判定"没界定出范围"，不输出），
    # 要么必须覆盖全部事件——绝不能只统计 top_k 那几条。
    rolls = [i for i in items if i.get("type") == "rollup"]
    assert not rolls or rolls[0]["count"] == len(events), \
        "统计若存在必须覆盖全部：%s" % (rolls[0]["count"] if rolls else None)


def test_time_window_floor_still_admits_zero_similarity(memdb, conn, monkeypatch):
    """窗内 0 相似的记忆也要能进来（「昨天做了什么」的保底不能失效）。"""
    from datetime import datetime
    occ = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    _mk(conn, "e0", "跟问题毫无词面关系的昨天事", occ, [0.0, 0.0, 1.0, 0.0])
    conn.commit()
    monkeypatch.setattr(recall.embed, "embed", lambda q, *a, **k: [1.0, 0.0, 0.0, 0.0])
    items = recall.recall(conn, "我昨天做了什么")
    assert any("昨天事" in i["gist"] for i in items)
