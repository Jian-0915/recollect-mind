"""F5 检索调用闭环测试（2026-11-19）：

覆盖三件事：
  1. 播报队列写入/消费（独立库，不写主库）
  2. 多路召回 recall()：整句 + 实体，去重提权
  3. F5 一轮查多个实体、每个都整理成短句写播报
全部纯本地、零网络、零主库写入。
"""
import json

import pytest

from neuromem import config, knowledge


@pytest.fixture
def kb(tmp_path):
    c = knowledge.connect(tmp_path / "kb.db")
    yield c
    c.close()


class R:
    """假的搜索结果，字段对齐 add_results 需要的。"""
    def __init__(self, title, url, snippet):
        self.title, self.url, self.snippet = title, url, snippet
        self.request_id = "fake"


# ---------------- 播报队列 ----------------

def test_brief_roundtrip(kb):
    assert knowledge.brief_stats(kb) == {"total": 0, "pending": 0}
    i = knowledge.add_brief(kb, entity="乌江寨", text="我查了那个寨子，在贵州遵义。",
                            sources=[{"title": "乌江寨", "url": "https://w.example"}])
    assert i > 0
    assert knowledge.brief_stats(kb)["pending"] == 1
    item = knowledge.peek_brief(kb)
    assert item and item["text"].startswith("我查了")
    assert item["entity"] == "乌江寨"
    assert item["sources"][0]["url"] == "https://w.example"
    # GET 即消费：再取就没有了
    assert knowledge.peek_brief(kb) is None
    assert knowledge.brief_stats(kb) == {"total": 1, "pending": 0}


def test_add_brief_ignores_empty(kb):
    assert knowledge.add_brief(kb, text="   ") == 0
    assert knowledge.peek_brief(kb) is None


def test_brief_in_independent_db(tmp_path, monkeypatch):
    """播报只写独立库，绝不碰主库 neuromem.db 的 events。"""
    kb_path = tmp_path / "kb.db"
    c = knowledge.connect(kb_path)
    knowledge.add_brief(c, entity="x", text="y")
    c.close()
    # 独立库确实有表
    import sqlite3
    c2 = sqlite3.connect(str(kb_path))
    names = {r[0] for r in c2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    c2.close()
    assert "discovery_brief" in names


# ---------------- 多路召回 ----------------

def test_recall_by_entity_and_sentence(kb):
    knowledge.add_results(kb, [
        R("乌江寨旅游攻略", "https://w.example", "乌江寨在贵州遵义，是古镇"),
        R("贵州小吃", "https://food.example", "酸汤鱼、丝娃娃"),
    ], embed_text=False)
    # 只给描述性整句，不带专名，也应召回相关条目
    hits = knowledge.recall(kb, "上次说的那个古镇在哪来着", top_k=5)
    assert any("乌江寨" in h.title for h in hits)
    # 专名 + 整句多路，去重后仍只有 2 条库内条目
    hits2 = knowledge.recall(kb, "乌江寨好玩吗", ["乌江寨"], top_k=5)
    assert len(hits2) <= 2
    assert len({h.id for h in hits2}) == len(hits2)   # 去重


def test_recall_multihit_boosts_score(kb):
    knowledge.add_results(kb, [R("乌江寨", "https://w.example", "贵州古镇")],
                          embed_text=False)
    one = knowledge.recall(kb, "乌江寨", top_k=3)
    both = knowledge.recall(kb, "乌江寨是什么", ["乌江寨"], top_k=3)
    # 命中两路的条目分数应被提权
    assert both[0].score >= one[0].score


# ---------------- F5 一轮多实体 + 整理成短句 ----------------

def test_run_once_multi_entities_and_brief(kb, monkeypatch):
    from neuromem import autoresearch as ar

    monkeypatch.setattr(config, "AUTORESEARCH", True)
    ar.set_enabled(True)
    monkeypatch.setattr(config, "AUTORESEARCH_PER_TURN_MAX", 3)
    monkeypatch.setattr(config, "AUTORESEARCH_DAILY", 50)

    class _NoClose:
        def __init__(self, real): self._real = real
        def __getattr__(self, k): return getattr(self._real, k)
        def close(self): pass
    monkeypatch.setattr(knowledge, "connect", lambda path=None: _NoClose(kb))

    # 两个未知实体，每个都能查到资料
    monkeypatch.setattr(ar, "discover", lambda text, c, private=(): [
        {"entity": "乌江寨", "reason": "未知地名"},
        {"entity": "酸汤鱼", "reason": "未知食物"},
    ])
    monkeypatch.setattr(ar, "should_search", lambda c, now=None: (True, "可以查"))

    class FakeResult:
        def __init__(self, title, url, snippet):
            self.title, self.url, self.snippet = title, url, snippet
            self.request_id = "fake"
    class FakeProvider:
        def search(self, query, max_results=5):
            return [FakeResult(query + "介绍", "https://" + query + ".example", "一段摘要")]
    # 模型整理：用一个可预测的假生成函数
    def fake_gen(prompt):
        return "我查了一下，挺有意思的"

    out = ar.observe("乌江寨和酸汤鱼", sync=True, provider=FakeProvider(),
                     generate_fn=fake_gen)
    assert out and out["entity"] in ("乌江寨", "酸汤鱼")
    # 两个实体都查了、都写了审计
    logs = knowledge.list_autoresearch(kb)
    assert len(logs) == 2
    assert {l["entity"] for l in logs} == {"乌江寨", "酸汤鱼"}
    # 两个实体都写了播报短句
    briefs = []
    for _ in range(3):
        b = knowledge.peek_brief(kb)
        if b:
            briefs.append(b)
    assert len(briefs) == 2
    assert all(b["text"] == "我查了一下，挺有意思的" for b in briefs)


def test_fallback_brief_when_no_model(monkeypatch):
    """generate_fn 不可用/为空时，summarize_discovery 退回资料首句。"""
    from neuromem import research
    hit = knowledge.KnowledgeHit(1, "乌江寨旅游攻略", "https://w.example",
                                 "贵州古镇", 0.5, "2026-01-01T00:00:00+08:00")
    text = research.summarize_discovery("乌江寨", [hit], generate_fn=lambda p: "")
    assert "乌江寨" in text


# ---------------- 端到端：查到的知识被下一轮对话召回 ----------------

def test_knowledge_recalled_into_next_chat(kb, monkeypatch):
    """用户强调的核心：再聊到相关的事，系统直接调用已查到的知识。

    模拟：独立库里已有她上次查的乌江寨资料 → 本轮用户用描述性说法提起
    → _knowledge_block_for 召回 → 拼出的块里带资料、标明来源、提示是她自己查的。
    """
    from agent import companion
    knowledge.add_results(kb, [
        R("乌江寨旅游攻略", "https://w.example", "乌江寨在贵州遵义，是古镇")
    ], embed_text=False)
    # companion 内部自己 connect()；注入指向测试库的连接
    class _NoClose:
        def __init__(self, real): self._real = real
        def __getattr__(self, k): return getattr(self._real, k)
        def close(self): pass
    monkeypatch.setattr(knowledge, "connect", lambda path=None: _NoClose(kb))

    block = companion._knowledge_block_for("上次说的那个古镇在哪来着")
    assert block, "描述性说法也应召回已查知识"
    assert "乌江寨" in block
    assert "自己查到的" in block          # 明确来源，防穿帮
    assert "https://w.example" in block   # 带出处


# ---------------- 前端链路契约 ----------------

def test_frontend_brief_wiring():
    """前端必须有：取播报的桥接 + 聊天页轮询显示，防链路被改回去。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "desktop" / "web"
    logic = (root / "logic.js").read_text(encoding="utf-8")
    app = (root / "assets" / "app.js").read_text(encoding="utf-8")
    assert "autoBrief" in logic and "/api/autoresearch/brief" in logic
    assert "pollKnowledgeBrief" in app
    assert "DeskAPI.autoBrief()" in app
    assert "msg__selfnote" in app


def test_draft_thinking_is_stripped_from_brief():
    """v48：模型把分步草稿当正文吐出时，播报只留口语句，不许草稿进气泡。"""
    from neuromem import research
    dirty = ("3.  **Drafting - Attempt 2:**\n"
             "    哎，我刚才顺手查了下茅台镇，那里有几千家酒厂，整个镇子都飘着酒香。\n"
             "    *Cr")
    out = research._clean_discovery_text(dirty)
    assert "Drafting" not in out and "*Cr" not in out and "**" not in out
    assert "酒香" in out


def test_all_draft_lines_falls_back_to_source():
    """整段都是草稿时清洗为空，summarize_discovery 走资料首句降级。"""
    from neuromem import research
    from neuromem.knowledge import KnowledgeHit
    hit = KnowledgeHit(1, "茅台镇", "https://x", "几千家酒厂", 0.9, "2026-11-19")
    out = research.summarize_discovery(
        "茅台镇", [hit],
        generate_fn=lambda p: "2. **Review the Materials:**\n  * [1]")
    assert "Review" not in out and "茅台镇" in out
