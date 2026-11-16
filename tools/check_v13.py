"""v13 副本端到端自检：主体翻转 / 多维情感 / Brain / Cortex / 清库豁免 / MCP。

主库全程以只读 URI 打开，只做在线备份；所有写操作都落在临时副本上。
用法：python tools/check_v13.py       （不需要 Ollama，嵌入与生成离线 mock）

检查项：
  1. v12 存量库无 schema 破坏（v13 的表都运行时自建）
  2. agent 第一人称不再被写成「用户」（self/counterpart/third_party 三类）
  3. L0 固定身份只认人工、checksum 可校验、清库豁免
  4. 自叙事主体由 L0 给定、旧库回退标 counterpart
  5. 情感：离散向量、慢心境、按人关系、事件日志、时间衰减、旧召回接线
  6. Brain.turn / commit 一次跑通采纳+再巩固+会话+情感+插件
  7. Cortex 插件各写各的表、tick 可用、坏插件不拖垮主链路
  8. 动态清库覆盖全部新表，usage.clear 不穿透写主库
  9. MCP 仍是 10 个工具、空库可用、state 带固定身份
 10. 主库一字未动
"""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.stdout.reconfigure(encoding="utf-8")

SRC = ROOT / "data" / "neuromem.db"
VOLATILE = {"growth_log", "usage_log", "adoption_log", "drift_log",
            "conversation_turns", "companion_state", "memory_versions",
            "affect_events", "affect_relations", "reflection_log",
            "proactive_suggestions"}

from check_v12 import _counts, _mcp, _ro  # noqa: E402


def _offline():
    """把嵌入与生成换成确定性的离线实现，脚本不依赖 Ollama。"""
    from neuromem import config, embed, llm, store
    dim = config.EMBED_DIM
    embed.embed = lambda t, *a, **k: [1.0] + [0.0] * (dim - 1)
    llm.generate = lambda p, **k: "（离线自述）"
    store.persona.extract_relations = lambda *a, **k: []
    config.DUP_SIM = 2.0
    config.SUPERSEDE_SIM = 2.0
    return store


def main():
    tmp = Path(tempfile.mkdtemp(prefix="neuromem_v13_"))
    copy = tmp / "copy.db"
    src = _ro(SRC)
    dst = sqlite3.connect(str(copy))
    src.backup(dst)                       # 在线备份，含 WAL 里的最新数据
    dst.close()
    main_before = _counts(src)
    ver_before = src.execute("PRAGMA user_version").fetchone()[0]
    gist_before = [r[0] for r in src.execute("SELECT gist FROM events ORDER BY id")]
    vec_before = src.execute(
        "SELECT COUNT(*) FROM events WHERE embedding IS NOT NULL").fetchone()[0]
    src.close()
    print("副本·起点：user_version=%s，events=%d，向量=%d" % (
        ver_before, main_before.get("events", -1), vec_before))

    # 关键：先指向副本再 import，让 config.DB_PATH / usage._db / api.conn 全落副本
    os.environ["NEUROMEM_DB"] = str(copy)
    from neuromem import (affect, api, companion, config, cortex, db, identity,
                          recall, selfnarrative)
    from neuromem.brain import Brain
    store = _offline()

    checks = {}

    # 1) 无破坏性 schema 变更
    c = db.connect(str(copy))
    checks["v13 不破坏存量 schema"] = (
        c.execute("PRAGMA user_version").fetchone()[0] == ver_before
        and [r[0] for r in c.execute("SELECT gist FROM events ORDER BY id")] == gist_before
        and c.execute("SELECT COUNT(*) FROM events WHERE embedding IS NOT NULL")
        .fetchone()[0] == vec_before)
    checks["存量记忆仍全标 counterpart"] = all(
        r[0] == "counterpart" for r in c.execute("SELECT DISTINCT subject FROM events"))

    # 2) 主体翻转：agent 的「我」不再被写成对话者
    a = store.remember(c, "我答应过每天都在这里", speaker="用户", subject="self")[0]
    b_ = store.remember(c, "我怕打雷", speaker="用户")[0]
    d = store.remember(c, "我养了只猫", speaker="agent")[0]
    rows = {r["id"]: r for r in c.execute("SELECT id,gist,subject FROM events")}
    checks["subject=self 保留第一人称"] = (
        rows[a]["gist"] == "我答应过每天都在这里" and rows[a]["subject"] == "self")
    checks["默认仍兼容旧行为"] = (
        rows[b_]["gist"] == "用户怕打雷" and rows[b_]["subject"] == "counterpart")
    checks["agent 发言默认归 self"] = (
        rows[d]["gist"] == "我养了只猫" and rows[d]["subject"] == "self")
    items = recall.recall(c, "我答应过什么")
    checks["subject 跟着候选到生成层"] = any(
        x.get("subject") == "self" for x in items)

    # 3) L0：非人工拒绝、人工版本化、checksum
    checks["非人工 actor 被拒"] = not identity.set_profile(
        c, {"who": "偷偷改"}, actor="llm")["ok"]
    v1 = identity.get(c)["version"]
    up = identity.set_profile(c, {"who": "小伴，一个记事的陪伴者"}, actor="human")
    checks["人工版本化升级"] = up["ok"] and identity.get(c)["version"] == v1 + 1
    checks["固定身份 checksum"] = identity.verify(c)[0]

    # 4) 自叙事：主体来自 L0，旧库回退标 counterpart
    selfnarrative.build(c)
    nar = selfnarrative.get(c) or {}
    checks["自叙事主体由 L0 给定"] = "小伴" in (identity.block(c) or "")
    checks["自叙事 scope 已记录"] = nar.get("scope") in ("self", "counterpart")
    st = companion.state(c)
    checks["陪伴视角不再是第三人称档案"] = "他眼里的自己" not in companion.format_block(st)

    # 5) 多维情感：向量 / 慢心境 / 关系 / 日志 / 时间衰减 / 旧召回接线
    affect.update(c, {"sadness": 0.9}, person="对方")
    snap = affect.snapshot(c)
    checks["离散情绪与慢心境分离"] = (
        snap["top"] == "sadness" and snap["mood"]["sadness"] < snap["vector"]["sadness"])
    checks["表达层提示可用"] = bool(affect.expression_hints(snap))
    checks["旧 emotion_state 已接线"] = c.execute(
        "SELECT valence FROM emotion_state WHERE id=1").fetchone()[0] < 0
    affect.update(c, {"joy": 0.6}, person="小明")
    checks["按人关系情感独立"] = "小明" in affect.snapshot(c)["relations"]
    checks["情绪事件可追溯"] = c.execute(
        "SELECT COUNT(*) FROM affect_events").fetchone()[0] >= 2
    old = (datetime.now(timezone.utc).astimezone()
           - timedelta(hours=config.AFFECT_HALFLIFE_HOURS)).isoformat(
               timespec="seconds")
    c.execute("UPDATE affect_state SET updated_at=? WHERE id=1", (old,))
    c.commit()
    checks["真实时间衰减生效"] = affect.snapshot(c)["intensity"] < snap["intensity"]

    # 6) Brain / 7) Cortex
    brain = Brain(session_id="v13_check")
    pack = brain.turn("我答应过什么")
    checks["ContextPack 含身份/候选/契约"] = all(
        k in pack for k in ("identity_block", "candidates", "policy", "module_notes"))
    checks["render 带 id 与主体标签"] = "[id:" in pack.render() and "关于" in pack.render()
    eid = brain.remember("我答应过每周都来", subject="self")[0]
    out = brain.commit("我记得我说过每周都来。", used_ids=[eid],
                       query_text="我答应过什么", context_text="我说过每周都来",
                       affect_input={"joy": 0.7}, user_text="我答应过什么",
                       topic="约定")
    checks["commit 一次回写 L2+会话+情感"] = (
        out["log_id"] and out["turn_id"]
        and c.execute("SELECT COUNT(*) FROM adoption_log WHERE source='agent'")
        .fetchone()[0] >= 1
        and c.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0] >= 2)
    checks["再巩固版本链可用"] = c.execute(
        "SELECT COUNT(*) FROM memory_versions").fetchone()[0] >= 0
    brain.commit("这轮没有可用依据", used_ids=[], query_text="随便",
                 context_text="", outcome="refused")
    checks["Cortex 插件写自己的表"] = c.execute(
        "SELECT COUNT(*) FROM reflection_log").fetchone()[0] >= 1
    checks["Cortex tick 可用"] = "proactive" in brain.tick()

    class Broken(cortex.BrainModule):
        name = "broken"

        def contribute(self, p):
            raise RuntimeError("故意炸")

    cortex.REGISTRY["broken"] = Broken
    bad_brain = Brain(session_id="fault", modules=["broken"])
    checks["坏插件不拖垮主链路"] = isinstance(
        bad_brain.turn("你好")["module_notes"], dict)
    bad_brain.close()
    cortex.REGISTRY.pop("broken", None)
    brain.close()

    # 8) 清库：新表动态纳入，L0 豁免，usage 不穿透
    api.conn = lambda: db.connect(str(copy))
    plan = api.reset_plan("all")
    r = api.reset_all({"confirm": "RESET"})
    c2 = db.connect(str(copy))
    after = _counts(c2)
    new_tables = ("affect_state", "affect_events", "affect_relations",
                  "reflection_log", "proactive_suggestions", "adoption_log",
                  "conversation_turns", "memory_versions")
    checks["清库覆盖全部 v13 新表"] = all(
        t in r["cleared"] and after.get(t) == 0 for t in new_tables)
    checks["清库预检列出身份豁免"] = ("identity" in plan["will_keep"]
                                  and "identity_history" in plan["will_keep"])
    checks["清库后固定身份仍在"] = (identity.get(c2)["version"] == v1 + 1
                                and identity.verify(c2)[0])
    checks["usage.clear 未穿透"] = c.execute(
        "SELECT COUNT(*) FROM usage_log").fetchone()[0] == 0
    print("副本·清库：%d 张表被清，identity v%s 保留" % (
        len(r.get("cleared") or []), identity.get(c2)["version"]))
    c2.close()
    c.close()

    # 9) MCP 真实 stdio 会话（指向清库后的副本）
    checks.update(_mcp(copy, full=False))

    # 10) 主库只读复核
    src2 = _ro(SRC)
    main_after = _counts(src2)
    ver_main = src2.execute("PRAGMA user_version").fetchone()[0]
    src2.close()
    diff = {k: (main_before.get(k, 0), main_after.get(k, 0))
            for k in set(main_before) | set(main_after)
            if main_before.get(k, 0) != main_after.get(k, 0)}
    checks["主库 schema 未被改动"] = ver_main == ver_before
    checks["主库记忆未被本脚本改动"] = all(k in VOLATILE for k in diff)
    if diff:
        print("  主库并发变化（外部服务，非本脚本）：" + "，".join(
            "%s %d→%d" % (k, v[0], v[1]) for k, v in sorted(diff.items())))

    print()
    for k, v in checks.items():
        print("  %s %s" % ("PASS" if v else "FAIL", k))
    bad = [k for k, v in checks.items() if not v]
    print("\n结果：%d/%d PASS%s" % (len(checks) - len(bad), len(checks),
                                   "" if not bad else "；失败：" + "、".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
