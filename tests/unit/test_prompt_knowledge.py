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
    """不注入时，提示词里没有「知识块」整段（能力认知那句是常驻的，不算知识块）。"""
    p = prompts.build_prompt(None, "你好", [], knowledge_block="")
    assert "【你自己查到的资料（不是对方说过的" not in p


def test_build_prompt_with_knowledge():
    class Hit:
        title, snippet, url = "乌江寨", "贵州古镇", "https://w.example"
    block = prompts.build_knowledge_block([Hit()])
    p = prompts.build_prompt(None, "乌江寨好玩吗", [], knowledge_block=block)
    assert block in p
    assert "乌江寨好玩吗" in p


def test_system_teaches_search_capability():
    """她不能再说「我没有搜索功能」——SYSTEM / NO_BRAIN 都要有能力认知。"""
    assert "没有搜索功能" in prompts.SYSTEM        # 作为反面禁令出现
    assert "自己去查" in prompts.SYSTEM
    assert "没有搜索功能" in prompts.NO_BRAIN_SYSTEM
