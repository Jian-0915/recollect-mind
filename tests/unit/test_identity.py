"""v12 回归：L0 固定身份层 + events.subject 主体归属。

重点验证三件事：
1. 固定身份只能人工版本化升级，LLM / 自进化 / 再巩固都写不进去；
2. 清库必须豁免固定身份（清的是经历，不是「我是谁」）；
3. v11 存量记忆平滑标 counterpart，不删数据、不改 gist。
"""
import json
import re
import sqlite3
from pathlib import Path

from neuromem import api, config, db, identity, store

ROOT = Path(__file__).resolve().parents[2]


def _offline(monkeypatch, dim=4):
    """不连模型的写入环境：固定向量 + 固定抽取结果。"""
    monkeypatch.setattr(store.embed, "embed",
                        lambda t, *a, **k: [1.0] + [0.0] * (dim - 1))
    monkeypatch.setattr(store.persona, "extract_relations", lambda *a, **k: [])
    # 固定向量会让所有句子相似度=1，被去重合并成同一条；这里只验 subject，
    # 所以把去重 / 取代闸门抬到不可能触发，保证四句各自成事件。
    monkeypatch.setattr(config, "DUP_SIM", 2.0)
    monkeypatch.setattr(config, "SUPERSEDE_SIM", 2.0)

    def _extract(text, base_date=None):
        return {"who": [], "when": "", "occurred_at": "", "where": "",
                "affect": {"valence": 0.0, "arousal": 0.0, "label": "中性"}}

    monkeypatch.setattr(store, "extract", _extract)


def test_default_identity_is_verified(memdb, conn):
    cur = identity.get(conn)
    assert cur["version"] == 1
    assert set(cur["profile"]) == set(identity.PROFILE_FIELDS)
    ok, msg = identity.verify(conn)
    assert ok, msg
    block = identity.block(conn)
    assert "我是谁" in block and "我服务谁" in block
    assert "不可被对话改写" in block


def test_only_human_can_upgrade_identity(memdb, conn):
    for bad in ("llm", "agent", "auto", "evolve", "drift", "reconsolidation", "model"):
        r = identity.set_profile(conn, {"who": "被偷偷改掉"}, actor=bad)
        assert not r["ok"], bad
    assert identity.get(conn)["version"] == 1

    r = identity.set_profile(conn, {"who": "一个记得住事的陪伴者"},
                              actor="human", reason="人工确认身份")
    assert r["ok"] and r["version"] == 2
    assert identity.get(conn)["profile"]["who"] == "一个记得住事的陪伴者"
    hist = identity.history(conn)
    assert hist and hist[0]["version"] == 1, "旧版本必须先归档再覆盖"
    ok, msg = identity.verify(conn)
    assert ok, msg


def test_checksum_detects_out_of_band_edit(memdb, conn):
    identity.get(conn)          # 先惰性建默认行，否则 UPDATE 影响 0 行
    forged = dict(identity.DEFAULT_PROFILE, who="绕过版本化直接改库")
    conn.execute("UPDATE identity SET profile_json=? WHERE id=1",
                 (json.dumps(forged, ensure_ascii=False, sort_keys=True),))
    conn.commit()
    ok, msg = identity.verify(conn)
    assert not ok and "checksum" in msg


def test_reset_keeps_identity(memdb, conn):
    conn.execute(
        "INSERT INTO events(id,gist,created_at) VALUES('e1','测试记忆','2026-11-15')")
    conn.commit()
    identity.set_profile(conn, {"tone": "简短温和"}, actor="human")
    before = identity.get(conn)["version"]

    plan = api.reset_plan("all")
    assert "identity" in plan["will_keep"]
    assert "identity_history" in plan["will_keep"]

    r = api.reset_all({"confirm": "RESET"})
    assert r["ok"], r
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert identity.get(conn)["version"] == before
    ok, msg = identity.verify(conn)
    assert ok, msg
    assert "identity" in api.export_all(), "备份必须带上固定身份"


def test_v11_migration_marks_existing_memories_counterpart(tmp_path):
    p = tmp_path / "v11.db"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE events (id TEXT PRIMARY KEY, gist TEXT)")
    c.execute("INSERT INTO events VALUES('old1','旧系统记录的是对方')")
    c.execute("PRAGMA user_version=11")
    c.commit()
    c.close()

    c2 = db.connect(str(p))
    # 版本号跟随后续 schema 演进（v13 起为 13），断言"已升到当前版本"而不是写死数字
    assert c2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    row = c2.execute("SELECT subject,gist FROM events WHERE id='old1'").fetchone()
    assert row["subject"] == config.DEFAULT_SUBJECT == "counterpart"
    assert row["gist"] == "旧系统记录的是对方", "迁移不得改写原话"
    c2.close()


def test_store_remember_writes_subject(memdb, conn, monkeypatch):
    _offline(monkeypatch)
    a = store.remember(conn, "我答应过每周都来", speaker="用户", subject="self")
    b = store.remember(conn, "他喜欢在雨天散步", speaker="用户")
    d = store.remember(conn, "去年一起去了海边", speaker="用户", subject="shared")
    e = store.remember(conn, "邻居家的猫很怕生", speaker="用户", subject="不合法")
    rows = {r["id"]: r["subject"] for r in conn.execute("SELECT id,subject FROM events")}
    assert all(rows[x] == "self" for x in a)
    assert all(rows[x] == "counterpart" for x in b), "不传时维持旧行为"
    assert all(rows[x] == "shared" for x in d)
    assert all(rows[x] == "counterpart" for x in e), "非法值应回落而非入库"


def test_agent_speaker_defaults_to_self(memdb, conn, monkeypatch):
    _offline(monkeypatch)
    ids = store.remember(conn, "我答应过明天再聊", speaker="agent")
    assert ids
    assert conn.execute("SELECT subject FROM events WHERE id=?", (ids[0],)).fetchone()[0] == "self"


def test_identity_has_no_automatic_writer(memdb):
    """机制证明：包内除人工入口外没有任何模块能写固定身份，且本层不接 LLM。"""
    allowed = {"identity.py", "api.py", "memory.py", "cli.py"}
    offenders = [f.name for f in (ROOT / "neuromem").glob("*.py")
                 if f.name not in allowed
                 and "set_profile(" in f.read_text(encoding="utf-8")]
    assert not offenders, "这些模块不应具备改写固定身份的能力：%s" % offenders
    src = (ROOT / "neuromem" / "identity.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+.*\bllm\b", src, re.M), \
        "固定身份层不得 import 生成模型"
    assert "generate(" not in src
