"""陪伴 Agent 实验台（v13 配套）。

这不是记忆模块本身，而是**第一个真正使用它的 agent**：进程内直连
``neuromem.brain.Brain``（不经过 MCP），自己调用生成模型、自己判断哪些候选
记忆真的用上了、再统一回传采纳。用它产生可复现、可对照的真实使用数据，
而不是靠脚本按规则填 used_ids。

    from agent.companion import CompanionAgent
    a = CompanionAgent()                 # 默认独立库 data/agent_lab.db
    print(a.step("我叫小林，在杭州做设计")["reply"])
    a.close()

命令行：``python -m agent --help``。
"""
import os
from pathlib import Path

# 必须在 import neuromem 之前：config.DB_PATH / usage_log / settings 默认都走
# 主库，真实跑剧本会产生写入。这里让 agent 进程整体指向独立实验库，主库零写入。
_LAB_DB = Path(__file__).resolve().parent.parent / "data" / "agent_lab.db"
os.environ.setdefault("NEUROMEM_DB", str(_LAB_DB))

from .companion import CompanionAgent  # noqa: E402

__all__ = ["CompanionAgent"]
