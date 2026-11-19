"""v41：已接入能力清单——与记忆分开、权威、可回答「接好没」。

用户实测（2026-11-19）：问「世界第一高桥在哪里」，她答「我没有网页搜索功能，
等微微接好」。根因不是记忆错——「阿剑正在给她接入搜索功能」是真实发生过的事实，
不该删。缺的是把「功能现在到底接好没」作为权威状态告诉她。
本模块验证：能力状态独立注入、措辞杜绝「我没有 XX 功能」、与记忆块区分。
"""
import pytest

from neuromem import capabilities as caps
from neuromem import config
from agent import prompts


def test_collect_core_capabilities():
    c = caps.collect()
    keys = {x.key for x in c}
    assert {"web_search", "auto_research", "knowledge_base", "memory"} <= keys
    # 核心能力默认 ready；桌面能力没传状态就不列（不瞎说）
    assert all(x.ready for x in c)
    assert "tts" not in keys and "notify" not in keys


def test_collect_respects_autoresearch_switch(monkeypatch):
    monkeypatch.setattr(config, "AUTORESEARCH", False)
    c = {x.key: x for x in caps.collect()}
    assert c["auto_research"].status == "off"
    # 联网查资料本身仍然 ready（开关只管自主后台查）
    assert c["web_search"].status == "ready"


def test_collect_desktop_capabilities_via_status_map():
    c = {x.key: x for x in caps.collect(status_map={
        "tts": "ready", "notify": "ready",
    })}
    assert c["tts"].ready and c["notify"].ready
    # tts 未就绪时如实标注 partial/off，而不是谎报
    c2 = {x.key: x for x in caps.collect(status_map={"tts": "off"})}
    assert not c2["tts"].ready
    assert "没启动" in c2["tts"].limit


def test_render_block_separates_capability_from_memory():
    block = caps.render_block(caps.collect())
    # 明确标注这不是记忆、不是对方说的话
    assert "不是记忆" in block
    assert "不是对方说过的话" in block or "不是对方说的" in block
    # 联网能力写明已接好 + 禁止说没有搜索功能
    assert "已经接好了" in block
    assert "联网查资料" in block
    assert "不许说" in block and "我没有搜索功能" in block


def test_prompt_injects_capability_block_before_candidates():
    block = caps.render_block(caps.collect())
    p = prompts.build_prompt(
        None, "世界第一高桥在哪里", [],
        capability_block=block,
    )
    assert "已接入的能力" in p
    assert "联网查资料" in p
    # 能力块出现在（本轮大脑上下文）候选之前——先给权威状态再看记忆
    pos_cap = p.index("已接入的能力")
    assert "本轮大脑上下文" not in p or pos_cap < p.index("本轮大脑上下文")


def test_no_capability_block_keeps_old_prompt():
    """不传能力块时，提示词与旧版一致（向后兼容 A/B 对照）。"""
    p1 = prompts.build_prompt(None, "你好", [])
    p2 = prompts.build_prompt(None, "你好", [], capability_block="")
    assert p1 == p2
    assert "已接入的能力" not in p1


def test_no_brain_prompt_accepts_capability_block():
    block = caps.render_block(caps.collect())
    p = prompts.build_prompt_no_brain(
        "身份", "世界第一高桥在哪里", [], capability_block=block)
    assert "联网查资料" in p
    assert "已接入的能力" in p
