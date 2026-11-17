"""查询改写接线的回归用例：改写只进向量通道，用户原话零污染。

核心契约（2026-11-17）：
- 共指问句用最近两轮「对方」原话改写**向量** query；
- 关键词/时间/实体通道、_last_query、pack['query']、commit 的 query_text
  全部保持原始文本——再巩固与采纳记录绝不能吃到上下文里的词。
"""
import pytest

from neuromem import config, recall
from neuromem.brain import Brain


class _Spy:
    """记录 recall 收到的 query 与 embed_query，然后返回空候选。"""

    def __init__(self):
        self.calls = []

    def __call__(self, conn, query, **kw):
        self.calls.append({"query": query,
                           "embed_query": kw.get("embed_query")})
        return []


@pytest.fixture()
def spy(monkeypatch):
    s = _Spy()
    monkeypatch.setattr(recall, "recall", s)
    return s


def _hist(counterpart):
    """最近对话：对方两句 + agent 一句（agent 的绝不能被拼进向量）。"""
    return [
        {"role": counterpart, "text": "今天心情好差啊", "ts": ""},
        {"role": counterpart, "text": "我摔倒了，流血了", "ts": ""},
        {"role": "agent", "text": "你还好吗", "ts": ""},
    ]


def test_rewrite_only_vector_channel(memdb, spy, conn):
    """改写后的上文只进 embed_query；原始 query 与其它字段不变。"""
    b = Brain(session_id="rw")
    cp = b.counterpart
    pack = b.turn("我为什么疼", history=_hist(cp))
    assert len(spy.calls) == 1
    c = spy.calls[0]
    # 原始 query 保持本轮原话
    assert c["query"] == "我为什么疼"
    assert pack["query"] == "我为什么疼"
    assert b._last_query == "我为什么疼"
    # 向量 query 拼了两轮对方原话，且没混进 agent 的话
    assert c["embed_query"] == "今天心情好差啊。我摔倒了，流血了。我为什么疼"
    assert "你还好吗" not in c["embed_query"]
    # trace 可观测
    assert pack["query_rewrite"] == "今天心情好差啊。我摔倒了，流血了"
    b.close()


def test_commit_query_never_rewritten(memdb, spy, conn):
    """commit 走 query_text / _last_query，都不含改写上文。"""
    b = Brain(session_id="rw2")
    cp = b.counterpart
    b.turn("我为什么疼", history=_hist(cp))
    # 显式 query_text 优先
    out = b.commit("你摔倒了", query_text="我为什么疼", used_ids=[])
    log = conn.execute(
        "SELECT query FROM adoption_log ORDER BY id DESC LIMIT 1").fetchone()
    assert log[0] == "我为什么疼"
    # 不带 query_text 时回退 _last_query，仍是原始
    b.turn("头还疼吗", history=_hist(cp))
    b.commit("嗯", used_ids=[])
    log2 = conn.execute(
        "SELECT query FROM adoption_log ORDER BY id DESC LIMIT 1").fetchone()
    assert log2[0] == "头还疼吗"
    b.close()


def test_no_history_no_rewrite(memdb, spy, conn):
    """不给 history 时完全维持旧行为：embed_query=None。"""
    b = Brain(session_id="rw3")
    b.turn("我为什么疼")
    assert spy.calls[0]["query"] == "我为什么疼"
    assert spy.calls[0]["embed_query"] is None
    assert not b.turn("你好")["query_rewrite"]
    b.close()


def test_switch_off_disables_rewrite(memdb, spy, conn, monkeypatch):
    """NEUROMEM_QUERY_REWRITE=0 时即使给了 history 也不改写。"""
    monkeypatch.setattr(config, "QUERY_REWRITE", False)
    b = Brain(session_id="rw4")
    b.turn("我为什么疼", history=_hist(b.counterpart))
    assert spy.calls[0]["embed_query"] is None
    b.close()


def test_greeting_history_not_piled(memdb, spy, conn):
    """寒暄轮不拼进向量，避免噪声。"""
    b = Brain(session_id="rw5")
    hist = [{"role": b.counterpart, "text": "嗯", "ts": ""}]
    b.turn("我为什么疼", history=hist)
    assert spy.calls[0]["embed_query"] is None
    b.close()
