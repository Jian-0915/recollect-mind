# -*- coding: utf-8 -*-
"""v23：概念生成「草稿链入库」污染的回归测试。

真实例子（主库 2026-11-18 的 2 条脏概念）：模型没关思考，把
"Draft 1/2/3 + 中英对照 + 15 chars. Good." 整段当概念吐出，367/467 字入库。
三层修复：调用处 think=False + 结构硬闸门 + 提取最后一句定稿的清洗兜底。
"""
import pytest

from neuromem import consolidator as cons


# 主库里两条脏概念的代表性片段（保留全部结构特征：编号/中英对照/字符自评/换行）
DRAFT_1 = (
    "*   Draft 1: 阿剑准备接入语音功能且冬天冷不喜欢。 "
    "(A Jian prepares to connect voice function and doesn't like cold winter.) - 18 characters. A bit clunky.\n"
    "    *   Draft 2: 阿剑想听我声音，冬天冷得浑身不舒服。 "
    "(A Jian wants to hear my voice, winter cold makes him uncomfortable.) - 17 characters.\n"
    "    *   Draft 3: 阿剑准备接入语音，冬天冷"
)
DRAFT_2 = (
    "4.  **Drafting Sentences (iterative process):**\n"
    "    *   Draft 1: 阿剑调试好久还没完成，自己都累了。 "
    "(A Jian debugged for a long time not finished, himself tired.) - 15 chars. Good.\n"
    "    *   Draft 2: 阿剑搞了好久，调试还没完成，累了。 "
    "(A Jian worked for a long time, debugging not finished, tired.) - 15 chars.\n"
    "    *   Draft 3: 阿剑调试好久未完成，自己都累了。 "
    "(A Jian debugged long time not finished, himself tired.) - 14 chars.\n"
    "    *   Draft 4: 阿剑搞了好久还没完成，自己都累了。"
)


def test_draft_dumps_rejected_by_validator():
    """两层旧防线都漏的草稿链，现在被结构闸门挡住。"""
    for raw in (DRAFT_1, DRAFT_2):
        assert cons._valid_concept(raw) is False


def test_normal_concepts_still_accepted():
    """硬闸门不能误杀正常概念（含短英文专名）。"""
    for good in (
        "阿剑调试好久还没完成，自己都累了",
        "用户借出的钱被拖了三个月才还",
        "妈妈把猫寄养在宠物酒店三天",
        "用户订阅了 SenseNova 之后延迟下降",
    ):
        assert cons._valid_concept(good) is True, good


def test_structural_noise_rejected_separately():
    """逐项验证硬闸门的五个检测点，避免只靠整段样例碰巧通过。"""
    assert cons._valid_concept("Draft 1: 阿剑喜欢音乐") is False          # 草稿编号
    assert cons._valid_concept("阿剑调试了很久，15 chars. Good.") is False  # 字符自评
    assert cons._valid_concept("阿剑喜欢音乐（A Jian likes music very much）") is False  # 中英对照
    assert cons._valid_concept("1. 阿剑调试了很久还没完成") is False       # 编号列表
    assert cons._valid_concept("阿剑调试好久还没完成" * 5) is False        # 超长


def test_clean_extracts_last_draft_sentence():
    """清洗兜底取最后一句定稿，而不是把整段草稿链入库。"""
    assert cons._clean_concept(DRAFT_2) == "阿剑搞了好久还没完成，自己都累了"


def test_clean_leaves_clean_text_untouched():
    good = "阿剑调试好久还没完成，自己都累了"
    assert cons._clean_concept(good) == good


def test_cleaned_output_passes_validator():
    """清洗结果必须是合法概念；提不出来时原样返回也会被拒收。"""
    for raw in (DRAFT_1, DRAFT_2):
        cleaned = cons._clean_concept(raw)
        assert cons._valid_concept(cleaned) is True, cleaned


def test_run_concepts_disables_think(conn, monkeypatch):
    """概念再抽象这条生成链路必须显式关思考（治本）。"""
    calls = []

    def fake_generate(prompt, **kw):
        calls.append(kw)
        return "用户反复调试后终于把服务跑通"

    monkeypatch.setattr(cons.llm, "generate", fake_generate)
    monkeypatch.setattr(cons.embed, "embed", lambda text: [1.0, 0.0])
    for i in range(3):
        conn.execute(
            "INSERT INTO concepts(id,text,abstraction_level,confidence,created_at,embedding) "
            "VALUES(?,?,?,?,?,?)",
            ("con_t%d" % i, "用户第%d次调试服务" % i, 1, 0.6,
             "2026-11-18T10:00:00+08:00", cons.db.pack([1.0, 0.0])),
        )
    conn.commit()
    cons.run_concepts(conn, min_cluster=3, sim_threshold=0.0)
    assert calls, "应发生概念生成调用"
    assert all(c.get("think") is False for c in calls), calls


def test_run_disables_think(conn, monkeypatch):
    """事件→概念（脏数据来源的那条链路）也必须显式关思考。"""
    calls = []

    def fake_generate(prompt, **kw):
        calls.append(kw)
        return "阿剑调试好久还没完成，自己都累了"

    monkeypatch.setattr(cons.llm, "generate", fake_generate)
    monkeypatch.setattr(cons.embed, "embed", lambda text: [1.0, 0.0])
    for i in range(2):
        conn.execute(
            "INSERT INTO events(id,gist,embedding,surprise,created_at,archived) "
            "VALUES(?,?,?,?,?,0)",
            ("ev_%d" % i, "阿剑调试了很久还没完成", cons.db.pack([1.0, 0.0]), 0.7,
             "2026-11-18T10:00:00+08:00"),
        )
    conn.commit()
    cons.run(conn, min_cluster=2, sim_threshold=0.0)
    assert calls, "应发生概念生成调用"
    assert all(c.get("think") is False for c in calls), calls


def test_run_stores_cleaned_sentence_not_draft_dump(conn, monkeypatch):
    """端到端：即便模型吐出草稿链，入库的也必须是清洗后的一句话。"""
    monkeypatch.setattr(cons.llm, "generate", lambda prompt, **kw: DRAFT_2)
    monkeypatch.setattr(cons.embed, "embed", lambda text: [1.0, 0.0])
    for i in range(2):
        conn.execute(
            "INSERT INTO events(id,gist,embedding,surprise,created_at,archived) "
            "VALUES(?,?,?,?,?,0)",
            ("ev_%d" % i, "阿剑调试了很久还没完成", cons.db.pack([1.0, 0.0]), 0.7,
             "2026-11-18T10:00:00+08:00"),
        )
    conn.commit()
    created = cons.run(conn, min_cluster=2, sim_threshold=0.0)
    assert created, "应生成概念"
    row = conn.execute("SELECT text FROM concepts WHERE id=?", (created[0]["id"],)).fetchone()
    assert row is not None and row["text"] == "阿剑搞了好久还没完成，自己都累了", row
