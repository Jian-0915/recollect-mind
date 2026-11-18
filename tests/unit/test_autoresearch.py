"""F5 触发闸测试：缺口发现 + 防打扰闸门，全部纯本地、零网络。"""
import os
from datetime import datetime, timedelta, timezone

import pytest

from neuromem import autoresearch, config, knowledge

# 北京 15:00 = UTC 07:00。别用 UTC 15:00——那是北京 23:00，会踩静默门。
_DAY = datetime(2026, 11, 18, 7, 0, tzinfo=timezone.utc).astimezone(
    timezone(timedelta(hours=8)))


@pytest.fixture
def kb(tmp_path):
    c = knowledge.connect(tmp_path / "kb.db")
    yield c
    c.close()


def test_discover_finds_unknown_entity(kb):
    found = autoresearch.discover("我今天去了乌江寨玩，还挺有意思", kb)
    assert any(x["entity"] == "乌江寨" for x in found)


def test_discover_skips_when_already_known(kb):
    class R:
        title, url, snippet, request_id = "乌江寨", "https://u.example", "摘要", "r"
    knowledge.add_results(kb, [R()], embed_text=False)
    found = autoresearch.discover("又提到乌江寨了", kb)
    assert all(x["entity"] != "乌江寨" for x in found)


def test_discover_respects_private_terms(kb):
    found = autoresearch.discover("微微说她去了 weighting 游戏", kb,
                                  private_terms=["微微"])
    assert all("微微" not in x["entity"] for x in found)


def test_gate_quiet_hours(kb, monkeypatch):
    monkeypatch.setattr(config, "AUTORESEARCH", True)
    quiet = datetime(2026, 11, 18, 15, 30, tzinfo=timezone.utc).astimezone(
        timezone(timedelta(hours=8)))  # 北京 23:30
    ok, why = autoresearch.should_search(kb, now=quiet)
    assert ok is False and "静默" in why


def test_gate_daily_cap(kb, monkeypatch):
    monkeypatch.setattr(config, "AUTORESEARCH", True)
    monkeypatch.setattr(config, "AUTORESEARCH_DAILY", 2)
    # log_autoresearch 用真实系统时间写 created_at；测试里直接插固定白天时间，
    # 免得深夜跑测时日志时间与 _DAY 对不上。
    for _ in range(2):
        kb.execute("INSERT INTO autoresearch_log(entity,created_at) VALUES(?,?)",
                   ("x", _DAY.isoformat()))
    kb.commit()
    ok, why = autoresearch.should_search(kb, now=_DAY)
    assert ok is False and "上限" in why


def test_gate_min_gap(kb, monkeypatch):
    monkeypatch.setattr(config, "AUTORESEARCH", True)
    monkeypatch.setattr(config, "AUTORESEARCH_DAILY", 10)
    monkeypatch.setattr(config, "AUTORESEARCH_GAP_MIN", 30)
    # 最近一条时间是 5 分钟前 → 间隔不够
    recent = (_DAY - timedelta(minutes=5)).isoformat()
    kb.execute("INSERT INTO autoresearch_log(entity,created_at) VALUES(?,?)",
               ("x", recent))
    kb.commit()
    ok, why = autoresearch.should_search(kb, now=_DAY)
    assert ok is False and ("太近" in why or "间隔" in why)


def test_gate_allows_in_daytime(kb, monkeypatch):
    monkeypatch.setattr(config, "AUTORESEARCH", True)
    ok, why = autoresearch.should_search(kb, now=_DAY)
    assert ok is True and why == "可以查"


def test_runtime_switch(kb, monkeypatch):
    monkeypatch.setattr(config, "AUTORESEARCH", True)
    autoresearch.set_enabled(False)
    assert autoresearch.enabled() is False
    ok, why = autoresearch.should_search(kb)
    assert ok is False and "关闭" in why
    autoresearch.set_enabled(True)
    assert autoresearch.enabled() is True


def test_config_off_blocks(kb, monkeypatch):
    monkeypatch.setattr(config, "AUTORESEARCH", False)
    ok, why = autoresearch.should_search(kb)
    assert ok is False and "关闭" in why


def test_observe_sync_searches_and_logs(kb, monkeypatch):
    """同步路径：对话文本里有未知实体 → 联网（假 provider）→ 存库 + 审计留痕。"""
    from neuromem import autoresearch as ar
    monkeypatch.setattr(config, "AUTORESEARCH", True)
    ar.set_enabled(True)
    # 把独立库指向测试 kb。生产代码自己开自己关，这里注入的连接 close 设成
    # no-op，避免 _run_once 的 finally 关掉测试还要读的 kb。
    class _NoClose:
        def __init__(self, real): self._real = real
        def __getattr__(self, k): return getattr(self._real, k)
        def close(self): pass
    monkeypatch.setattr(knowledge, "connect", lambda path=None: _NoClose(kb))

    class FakeResult:
        def __init__(self, title, url, snippet):
            self.title, self.url, self.snippet = title, url, snippet
            self.request_id = "fake"
    class FakeProvider:
        def search(self, query, max_results=5):
            return [FakeResult("乌江寨介绍", "https://w.example", "贵州古镇")]
    # 用白天时间过静默闸
    monkeypatch.setattr(ar, "should_search", lambda c, now=None: (True, "可以查"))
    monkeypatch.setattr(ar, "discover",
                        lambda text, c, private=(): [{"entity": "乌江寨", "reason": "未知地名"}])

    out = ar.observe("今天去了乌江寨", sync=True, provider=FakeProvider())
    assert out and out["entity"] == "乌江寨"
    logs = knowledge.list_autoresearch(kb)
    assert len(logs) == 1 and logs[0]["entity"] == "乌江寨"
    assert logs[0]["hit_count"] >= 0


def test_observe_noop_when_disabled(kb, monkeypatch):
    monkeypatch.setattr(config, "AUTORESEARCH", False)
    assert autoresearch.observe("今天去了乌江寨", sync=True) is None
    assert knowledge.list_autoresearch(kb) == []


def test_observe_noop_without_entity(kb, monkeypatch):
    monkeypatch.setattr(config, "AUTORESEARCH", True)
    autoresearch.set_enabled(True)
    monkeypatch.setattr(knowledge, "connect", lambda path=None: kb)
    assert autoresearch.observe("你好呀今天心情不错", sync=True) is None
