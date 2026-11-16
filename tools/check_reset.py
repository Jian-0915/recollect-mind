"""清库自检：在**主库在线副本**上验证 /api/reset 的覆盖范围 + MCP 空库可用。

绝不写主库：脚本启动后先把主库在线备份成临时副本，并设置 NEUROMEM_DB 指向它，
本进程、usage 长连接、MCP 子进程都只会读写副本。跑完删除副本。

用法：
  python tools/check_reset.py            # 不依赖 Ollama 的 8 个工具调用
  python tools/check_reset.py --full     # 额外跑 memory_remember / memory_ask
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _ro(path):
    """主库一律只读 URI 打开：从机制上保证本脚本不可能写主库。"""
    return sqlite3.connect("file:%s?mode=ro" % Path(path).as_posix(),
                           uri=True, timeout=15)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "neuromem.db"
MCP = ROOT / "tools" / "mcp_server.py"
# v12：identity / identity_history 是 L0 固定身份层（人工设定的宪法），
# 与 api.RESET_KEEP 对应，清库必须豁免 —— 它们非空不算残留。
KEEP = {"settings", "accounts", "sessions", "users",
        "identity", "identity_history"}


def table_names(c):
    return [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def counts(c):
    return {t: c.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            for t in table_names(c)}


def show(tag, d):
    nz = {k: v for k, v in d.items() if v}
    print("[%s] 非空表 %d 张：%s" % (tag, len(nz),
          "，".join("%s=%d" % (k, v) for k, v in sorted(nz.items())) or "（全空）"))


def mcp_session(copy, with_write=True, full=False, tag="MCP"):
    env = dict(os.environ)
    env["NEUROMEM_DB"] = str(copy)
    p = subprocess.Popen([sys.executable, str(MCP)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", bufsize=1, env=env)

    def send(obj):
        p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        p.stdin.flush()
        line = p.stdout.readline()
        return json.loads(line) if line else {}

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "reset-check", "version": "0"}}})
    lst = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = [t["name"] for t in lst.get("result", {}).get("tools", [])]
    print("[%s] tools/list: %d 个工具 %s" % (tag, len(tools), "PASS" if len(tools) >= 10 else "FAIL"))

    calls = [
        ("memory_retrieve", {"query": "最近有什么值得聊的", "top_k": 3}),
        ("memory_person", {"name": "阿哲"}),
        ("memory_recent", {"days": 7}),
        ("memory_status", {"verbose": True}),
        ("memory_state", {"turns": 3}),
        ("memory_surface", {"limit": 3}),
    ]
    if with_write:
        calls += [
            ("memory_turn", {"text": "清库自检的一轮对话", "speaker": "用户", "topic": "自检"}),
            ("memory_feedback", {"used_ids": ["evt_not_exist"], "query_text": "自检",
                                 "context_text": "空库下的无效 id 不应报错",
                                 "outcome": "answered"}),
        ]
    if full:
        calls += [
            ("memory_remember", {"text": "清库自检：我今天去了一趟公园。"}),
            ("memory_ask", {"query": "我今天去哪了"}),
        ]

    failures = []
    for name, args in calls:
        r = send({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                  "params": {"name": name, "arguments": args}})
        res = r.get("result") or {}
        text = (res.get("content") or [{}])[0].get("text", "")
        ok = bool(res) and not res.get("isError")
        if not ok:
            failures.append(name)
        print("  %s %-18s %s" % ("PASS" if ok else "FAIL", name,
                                 text.replace("\n", " | ")[:100]))
    p.stdin.close()
    try:
        p.wait(timeout=20)
    except subprocess.TimeoutExpired:
        p.kill()
        failures.append("进程未退出")
    err = p.stderr.read().strip()
    if err:
        print("  stderr:", err[:200])
    return len(tools) >= 10 and not failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="额外跑需要 Ollama 的写入/问答工具")
    a = ap.parse_args()

    copy = Path(tempfile.gettempdir()) / ("neuromem-reset-check-%d.db" % os.getpid())
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(copy) + suffix)
        if f.exists():
            f.unlink()

    src = _ro(SRC)
    dst = sqlite3.connect(str(copy))
    src.backup(dst)                      # 在线备份，包含 WAL 里的新数据
    src.close()
    dst.close()

    os.environ["NEUROMEM_DB"] = str(copy)
    sys.path.insert(0, str(ROOT))
    from neuromem import api, config, db
    assert Path(config.DB_PATH).resolve() == copy.resolve(), "副本环境未生效，拒绝继续"

    main_ro = _ro(SRC)
    main_before = counts(main_ro)
    main_ro.close()

    c = sqlite3.connect(str(copy))
    show("副本·清库前", counts(c))
    c.close()
    ok_tools = mcp_session(copy, with_write=True, full=a.full, tag="清库前 MCP")

    # 探针：保证每张新表在清库前确实有数据，并建一张「以后才会有的表」
    # 验证动态枚举，而不是靠手工清单碰巧列全。
    c = sqlite3.connect(str(copy))
    c.execute("INSERT INTO memory_versions(event_id,revision,gist,created_at) "
              "VALUES('evt_probe',1,'再巩固探针',?)", (_now(),))
    c.execute("CREATE TABLE IF NOT EXISTS future_probe(id TEXT, note TEXT)")
    c.execute("INSERT INTO future_probe(id,note) VALUES('x','未来机制表探针')")
    c.commit()
    c.close()

    r = api.reset_all({"confirm": "RESET"})
    print("\n[/api/reset] ok=%s 清了 %d 张表，保留 %s" %
          (r.get("ok"), len(r.get("cleared") or []), r.get("kept")))
    print("  新机制表：" + "，".join(
        "%s(%s)" % (t, r.get("counts", {}).get(t))
        for t in ("adoption_log", "conversation_turns", "memory_versions",
                  "companion_state", "event_tags", "entity_alias", "future_probe")))
    print("  情绪重置 / 图缓存 / WAL / 用量：%s / %s / %s / %s" %
          ("平静" if r.get("ok") else "?", r.get("graph_invalidated"),
           r.get("checkpointed"), r.get("usage_cleared")))

    c = sqlite3.connect(str(copy))
    after = counts(c)
    show("副本·清库后", after)
    seq = c.execute("SELECT COUNT(*) FROM sqlite_sequence "
                    "WHERE name NOT LIKE 'identity%'").fetchone()[0]
    emo = c.execute("SELECT valence,arousal,label FROM emotion_state WHERE id=1").fetchone()
    c.close()
    dirty = {k: v for k, v in after.items() if v and k not in KEEP and k != "emotion_state"}
    checks = {
        "reset ok": bool(r.get("ok")),
        "业务表全空": not dirty,
        "新机制表在清单里": all(t in (r.get("cleared") or []) for t in
                          ("adoption_log", "conversation_turns", "memory_versions",
                           "companion_state", "event_tags", "entity_alias")),
        "未来新表自动纳入": "future_probe" in (r.get("cleared") or [])
                      and r.get("counts", {}).get("future_probe") == 1,
        "固定身份豁免清库": after.get("identity") == 1,
        "自增归零": seq == 0,
        "情绪平静": emo is not None and emo[2] == "平静" and emo[0] == 0,
        "MCP 10 工具": ok_tools,
    }

    # 清库后再次用 MCP 真实写一轮新数据（会话 / 采纳），然后二次清库
    print("\n[清库后 MCP] 重新拉起进程，模拟宿主重连：")
    ok_after = mcp_session(copy, with_write=True, full=False, tag="空库 MCP")
    r2 = api.reset_all({"confirm": "RESET"})
    c = sqlite3.connect(str(copy))
    after2 = counts(c)
    c.close()
    checks["空库 MCP 可用"] = ok_after
    checks["二次清库干净"] = bool(r2.get("ok")) and not {
        k: v for k, v in after2.items() if v and k not in KEEP and k != "emotion_state"}

    main_ro = _ro(SRC)
    main_after = counts(main_ro)
    main_ro.close()
    # HTTP 服务的自动进化线程、用量日志会在验证期间自然增长，这些不算脚本写入。
    volatile = {"growth_log", "usage_log", "adoption_log", "drift_log",
                "conversation_turns", "companion_state"}
    diff = {k: (main_before.get(k, 0), main_after.get(k, 0))
            for k in set(main_before) | set(main_after)
            if main_before.get(k, 0) != main_after.get(k, 0)}
    if diff:
        print("  主库并发变化（外部进程，非本脚本）：" + "，".join(
            "%s %d→%d" % (k, v[0], v[1]) for k, v in sorted(diff.items())))
    checks["主库记忆未被本脚本改动"] = all(k in volatile for k in diff)

    print("\n==== 结论 ====")
    for k, v in checks.items():
        print("  %s %s" % ("PASS" if v else "FAIL", k))
    if diff:
        print("  注：主库的 growth_log / usage_log 增长来自运行中的 HTTP 自动进化，"
              "本脚本全程以只读 URI 打开主库，只写临时副本。")
    if dirty:
        print("  残留表：", dirty)

    for suffix in ("", "-wal", "-shm"):
        f = Path(str(copy) + suffix)
        try:
            if f.exists():
                f.unlink()
        except OSError:
            pass
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
