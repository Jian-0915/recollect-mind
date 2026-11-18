"""答问链测试：全假 provider / 假模型，不发网络请求。"""
import pytest

from neuromem import knowledge, research
from neuromem.tools import SearchResult


class FakeProvider:
    def __init__(self, results=None, raise_error=False):
        self.results = results or []
        self.raise_error = raise_error
        self.calls = []

    def search(self, query, max_results=5):
        self.calls.append(query)
        if self.raise_error:
            raise RuntimeError("network down")
        return self.results

    def extract(self, url):
        return ""


@pytest.fixture
def kb(tmp_path):
    c = knowledge.connect(tmp_path / "kb.db")
    yield c
    c.close()


def _result(url="https://wujiang.example"):
    return SearchResult("乌江寨官网", url, "贵州乌江寨，吊脚楼与非遗打铁花",
                        "2026-01-01", "req1")


def test_no_entity_skips_search(kb):
    p = FakeProvider([_result()])
    r = research.research("今天心情不错", kb_conn=kb, provider=p,
                          generate_fn=lambda p: "答")
    assert not r.need_search and not r.sources and not p.calls


def test_first_time_searches_and_answers(kb):
    p = FakeProvider([_result()])
    r = research.research("乌江寨是什么地方", kb_conn=kb, provider=p,
                          generate_fn=lambda p: "乌江寨在贵州 [1]")
    assert r.need_search and r.searched and not r.used_cache
    assert "乌江寨" in r.answer
    assert len(r.sources) >= 1 and r.sources[0].url.endswith("wujiang.example")
    assert p.calls and "乌江寨" in p.calls[0]


def test_second_time_uses_cache_no_network(kb):
    p = FakeProvider([_result()])
    research.research("乌江寨是什么地方", kb_conn=kb, provider=p,
                      generate_fn=lambda p: "第一次")
    p2 = FakeProvider([_result()])
    r = research.research("乌江寨好玩吗", kb_conn=kb, provider=p2,
                          generate_fn=lambda p: "第二次")
    assert r.used_cache and not r.searched and not p2.calls
    assert "第二次" in r.answer


def test_network_failure_degrades_without_fabrication(kb):
    p = FakeProvider(raise_error=True)
    r = research.research("乌江寨是什么地方", kb_conn=kb, provider=p,
                          generate_fn=lambda p: "Never")
    assert r.need_search and not r.sources
    assert "没查到" in r.note
    assert r.answer == ""


def test_llm_failure_falls_back_to_source_list(kb):
    p = FakeProvider([_result()])
    def boom(prompt):
        raise RuntimeError("llm dead")
    r = research.research("乌江寨是什么地方", kb_conn=kb, provider=p,
                          generate_fn=boom)
    assert r.sources and "https://" in r.answer


def test_private_terms_not_sent(kb):
    p = FakeProvider([_result()])
    research.research("微微带我去了乌江寨", kb_conn=kb, private_terms=["微微"],
                      provider=p, llm_fn=lambda prompt, **kw: {},
                      generate_fn=lambda p: "答")
    assert p.calls
    assert "微微" not in p.calls[0]


def test_force_query_when_no_entity(kb):
    p = FakeProvider([_result()])
    r = research.research("查一下这个", kb_conn=kb, provider=p, force=True,
                          generate_fn=lambda p: "答")
    assert r.need_search and r.searched


def test_to_dict_shape(kb):
    p = FakeProvider([_result()])
    r = research.research("乌江寨是什么", kb_conn=kb, provider=p,
                          generate_fn=lambda p: "答")
    d = r.to_dict()
    assert set(d) >= {"answer", "sources", "need_search", "used_cache", "searched"}
    assert set(d["sources"][0]) >= {"title", "url", "snippet", "fetched_at"}
