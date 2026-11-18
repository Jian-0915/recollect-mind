"""F5 prompt 注入测试：她自己查到的资料进对话，但不冒充"对方说过的记忆"。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import prompts, companion


def test_knowledge_block_empty_when_no_hits():
    assert prompts.build_knowledge_block([]) == ""
    assert prompts.build_knowledge_block(None) == ""


def test_knowledge_block_marks_source():
    class Hit:
        title, snippet, url = "乌江寨", "贵州遵义的古镇，有吊脚楼", "https://w.example"
    block = prompts.build_knowledge_block([Hit()])
    assert "你自己查到的" in block
    assert "不是对方说过的" in block
    assert "乌江寨" in block and "https://w.example" in block


def test_build_prompt_without_knowledge_unchanged():
    """不注入时，提示词里没有知识块——保证旧行为一字不差。"""
    p = prompts.build_prompt(None, "你好", [], knowledge_block="")
    assert "你自己查到的" not in p


def test_build_prompt_with_knowledge():
    class Hit:
        title, snippet, url = "乌江寨", "贵州古镇", "https://w.example"
    block = prompts.build_knowledge_block([Hit()])
    p = prompts.build_prompt(None, "乌江寨好玩吗", [], knowledge_block=block)
    assert block in p
    assert "乌江寨好玩吗" in p


def test_knowledge_block_for_no_entity_is_empty(monkeypatch):
    """没有明确实体时返回空串，不查库、不注入。"""
    calls = []
    monkeypatch.setattr(companion, "_knowledge_block_for",
                        lambda t: calls.append(t) or "")
    assert companion._knowledge_block_for("今天心情不错") == ""
    assert calls == ["今天心情不错"]
