"""近因效应：刚写入的记忆要进回答块（P1-15）。"""
from datetime import datetime, timedelta

from neuromem import answer


def _iso(dt):
    return dt.astimezone().isoformat(timespec="seconds")


def test_recent_item_moves_to_front():
    now = datetime.now()
    old = [{"id": "o%d" % i, "gist": "旧记忆%d" % i, "kind": "verified",
            "created_at": _iso(now - timedelta(days=2))} for i in range(5)]
    fresh = {"id": "new", "gist": "刚写的", "kind": "verified",
             "created_at": _iso(now - timedelta(seconds=30))}

    out = answer.prioritize(old + [fresh], now=now)
    assert out[0]["id"] == "new", "最近写入的应排在第一位"
    assert len(out) == 6, "6 条上限"


def test_no_recent_keeps_order():
    now = datetime.now()
    items = [{"id": "o%d" % i, "gist": "旧", "kind": "verified",
              "created_at": _iso(now - timedelta(days=3))} for i in range(4)]
    out = answer.prioritize(items, now=now)
    assert [x["id"] for x in out] == ["o0", "o1", "o2", "o3"]


def test_old_fresh_is_ignored():
    now = datetime.now()
    items = [{"id": "a", "gist": "x", "created_at": _iso(now - timedelta(hours=5))},
             {"id": "b", "gist": "y", "created_at": _iso(now - timedelta(hours=4))}]
    out = answer.prioritize(items, now=now)
    assert [x["id"] for x in out] == ["a", "b"], "超过 1 小时不算新鲜"


def test_limit_and_missing_time():
    now = datetime.now()
    items = [{"id": "n%d" % i, "gist": "x"} for i in range(10)]
    out = answer.prioritize(items, now=now)
    assert len(out) == 6
    assert out[0]["id"] == "n0", "没有时间字段时保持原顺序"