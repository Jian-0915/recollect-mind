"""已接入能力清单（v41）——告诉她「自己现在能做什么」，与记忆严格分开。

为什么单独一个模块（2026-11-19）：
  用户实测问「世界第一高桥在哪里」，她答「我现在还没有网页搜索功能，
  等微微把搜索功能给我接好」。根因不是记忆错——主库里「阿剑正在给她接入
  搜索功能」是**真实发生过的事实**，不该删也不该屏蔽。真正缺的是：
  系统从没把「这个功能现在到底接好没有」作为**权威状态**告诉她。
  于是她只能从记忆候选里推断，把一条「正在接入」的历史记成「还没接好」。

设计原则：
  - 能力状态是**系统事实**，来源是代码/config/设置，不是记忆库、不是对话；
  - 注入时用独立 prompt 块，和【候选记忆】明确区分，标注「这不是记忆、
    也不是对方说过的话」，防止她把能力状态当回忆复述；
  - 每条带 status（ready/off/partial）和用法边界，让她能确切回答
    「这个已经接好了 / 还没接」，而不是含糊说「我没有这个功能」。

桌面专属能力（TTS / 系统通知）由调用方通过 status_map 传入——核心库不 import
desktop 包，网页后台与桌面壳可用各自的状态填充。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Capability:
    key: str
    name: str
    status: str          # ready | off | partial
    how: str             # 具备时能用来做什么（给模型的动作指引）
    limit: str = ""      # 边界 / 不具备时怎么如实说

    @property
    def ready(self) -> bool:
        return self.status == "ready"


# 状态文案：注入 prompt 用，措辞统一，避免模型自由发挥说成"没有这个功能"
_STATUS_LABEL = {
    "ready": "已经接好了，可以用",
    "partial": "部分可用",
    "off": "还没接好 / 当前没开",
}


def collect(conn=None, *, status_map=None) -> list[Capability]:
    """收集当前能力状态。

    conn: 主库连接（读 MCP/搜索服务配置用），None 时按默认配置判定。
    status_map: 调用方提供的运行时状态，如 {"tts": "ready", "notify": "ready"}。
               桌面壳从 tts.status()/notify 探测后传入；核心库不依赖 desktop。
    """
    from . import config

    status_map = dict(status_map or {})
    caps: list[Capability] = []

    # 1) 联网查外部资料（AnySearch MCP / 用户自配的搜索服务）
    caps.append(Capability(
        key="web_search",
        name="联网查资料",
        status=status_map.get("web_search", "ready"),
        how="遇到不认识的名词、地名、梗、事实性问题，你可以自己去联网查，"
            "查到后会有【你自己查到的资料】出现在上下文里，用里面的内容回答，并带出处。",
        limit="就算这一轮还没查到，也要说「我查查看」，不许说「我没有搜索功能」"
              "「我还没联网」「等别人把搜索给我接好」。",
    ))

    # 2) 自主后台查询（F5）
    caps.append(Capability(
        key="auto_research",
        name="自己发现并后台去查",
        status=(status_map.get("auto_research")
                or ("ready" if config.AUTORESEARCH else "off")),
        how="对话里遇到你不懂的东西，你会在后台自己去查，过一会儿聊天页会冒出"
            "一条「她自己查到的」消息。",
        limit="这是自动的，不用对方帮你开。",
    ))

    # 3) 独立外部知识库检索
    caps.append(Capability(
        key="knowledge_base",
        name="记住自己查过的东西",
        status=status_map.get("knowledge_base", "ready"),
        how="你查过的资料会存进你自己的知识库，下次再聊到相关的事，你能直接调出来用。",
        limit="这些是你自己查的资料，不是对方说过的记忆，别混为一谈。",
    ))

    # 4) 长期记忆（陪伴记忆库）
    caps.append(Capability(
        key="memory",
        name="记住对方说过的事",
        status=status_map.get("memory", "ready"),
        how="对方告诉过你的事、你们之间发生过的事，你会记在心里，聊到时能想起来。",
        limit="",
    ))

    # 5) 桌面专属能力：语音播报 / 系统通知（由调用方传状态；没传就不列，避免瞎说）
    if "tts" in status_map:
        caps.append(Capability(
            key="tts",
            name="把话念出来",
            status=status_map["tts"],
            how="你可以用自己的声音把回复念给对方听；对方也能调音色、语速、音量。",
            limit=("" if status_map["tts"] == "ready"
                   else "现在语音服务还没启动，别主动承诺能念出来。"),
        ))
    if "notify" in status_map:
        caps.append(Capability(
            key="notify",
            name="发系统通知",
            status=status_map["notify"],
            how="有新的回复时你能弹一条系统通知，对方点一下就回来看。",
        ))

    return caps


def render_block(caps: list[Capability] | None = None, *, status_map=None) -> str:
    """生成注入 prompt 的能力块。没有能力就返回空串（向后兼容）。

    关键措辞：
      - 明确「这是你当前的能力状态，不是记忆、也不是对方说的话」——
        防止她把「阿剑正在给我接搜索功能」这类历史当成「还没接好」；
      - 给 ready 能力的用法和 off 能力的如实说法，杜绝「我没有这个功能」。
    """
    if caps is None:
        caps = collect(status_map=status_map)
    if not caps:
        return ""

    lines = [
        "【你当前已接入的能力（这是系统此刻的真实状态，不是记忆，也不是对方说过的话）】",
        "下面列出你现在到底哪些功能能用。回答自己会不会、有没有某功能时，以这里为准，"
        "不要从往事里推断，也不要说「我还没有 XX 功能 / 等我接好」这类话。\n"
        "这是系统状态，不是你记得的事：不许说「我记得搜索功能接好了」，"
        "要说「我现在能查」。对方问你怎么知道的，也别拿这里的内容当记忆。",
    ]
    for c in caps:
        if c.ready or c.status == "partial":
            lines.append("- %s：%s。%s" % (c.name, _STATUS_LABEL[c.status], c.how))
        else:
            lines.append("- %s：%s。" % (c.name, _STATUS_LABEL.get(c.status, c.status)))
        if c.limit:
            lines.append("  %s" % c.limit)
    return "\n".join(lines)
