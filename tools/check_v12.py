"""v12 副本端到端自检：主库全程只读，只在临时副本上迁移 / 清库 / 跑 MCP。

用法：
    python tools/check_v12.py            # 不依赖 Ollama
    python tools/check_v12.py --full     # 额外验 MCP 写入 + L2 采纳回传

检查项：v11→v12 存量记忆平滑标记、向量与 gist 无损、L0 固定身份只认人工、
清库豁免固定身份、MCP 10 工具在迁移后空库可用、主库一字未动。
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

SRC = ROOT / "data" / "neuromem.db"
VOLATILE = {"growth_log", "usage_log", "adoption_log", "drift_log",
            "conversation_turns", "companion_state", "memory_versions"}


def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % Path(path).as_posix(),
                          uri=True, timeout=15)


def _counts(c):
    names = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    out = {}
    for n in names:
        try:
            out[n] = c.execute('SELECT COUNT(*) FROM "%s"' % n).fetchone()[0]
        except sqlite3.Error:
            out[n] = -1
    return out


def _mcp(copy_path, full=False):
    """真实 stdio 子进程，环境变量把 MCP 指向副本库。"""
    env = dict(os.environ, NEUROMEM_DB=str(copy_path))
    p = subprocess.Popen([sys.executable, str(ROOT / "tools" / "mcp_server.py")],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         bufsize=1, env=env)

    def send(obj):
        p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        p.stdin.flush()
        line = p.stdout.readline()
        return json.loads(line) if line else {}

    try:
        init = send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                "clientInfo": {"name": "check_v12", "version": "0"}}})
        ok_init = init.get("result", {}).get("serverInfo", {}).get("name") == "neuromem"
        lst = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = lst.get("result", {}).get("tools", [])
        names = [t["name"] for t in tools]
        schema = next((t.get("inputSchema", {}) for t in tools
                       if t["name"] == "memory_remember"), {})
        has_subject = "subject" in (schema.get("properties") or {})

        def call(name, args):
            r = send({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                      "params": {"name": name, "arguments": args}})
            res = r.get("result") or {}
            return (res.get("content") or [{}])[0].get("text", ""), not res.get("isError")

        st, ok_state = call("memory_state", {})
        rt, ok_rt = call("memory_retrieve", {"query": "随便问一句", "top_k": 3})
        checks = {
            "MCP initialize": ok_init,
            "MCP 仍是 10 个工具": len(tools) == 10,
            "memory_remember 支持 subject": has_subject,
            "memory_state 输出固定身份": ok_state and "固定身份" in st and "我是谁" in st,
            "空库检索不报错": ok_rt and "没有匹配" in rt,
        }
        if full:
            txt, ok_w = call("memory_remember", {"text": "我答应过每天都在这里",
                                                 "subject": "self"})
            fb, ok_f = call("memory_feedback",
                            {"used_ids": [], "outcome": "refused",
                             "query_text": "随便问一句"})
            checks["MCP 写入 subject=self"] = ok_w and "已记住" in txt
            checks["MCP 空采纳仍落日志"] = ok_f
        return checks
    finally:
        try:
            p.stdin.close()
            p.wait(timeout=12)
            err = p.stderr.read()
            if err.strip():
                print("  MCP stderr:", err.strip()[:200])
        except Exception:
            p.kill()


def main():
    full = "--full" in sys.argv
    tmp = Path(tempfile.mkdtemp(prefix="neuromem_v12_"))
    copy = tmp / "copy.db"

    # 1) 主库只读在线备份（含 WAL 里的最新数据）
    src = _ro(SRC)
    dst = sqlite3.connect(str(copy))
    src.backup(dst)
    dst.close()
    main_before = _counts(src)
    ver_before = src.execute("PRAGMA user_version").fetchone()[0]
    gist_before = [r[0] for r in src.execute("SELECT gist FROM events ORDER BY id")]
    vec_before = src.execute(
        "SELECT COUNT(*) FROM events WHERE embedding IS NOT NULL").fetchone()[0]
    src.close()

    tmp_c = sqlite3.connect(str(copy))
    ver0 = tmp_c.execute("PRAGMA user_version").fetchone()[0]
    tmp_c.close()
    print("副本·迁移前：user_version=%s，events=%d，向量=%d" % (
        ver0, main_before.get("events", -1), vec_before))

    # 关键：先指向副本再 import，让 config.DB_PATH / usage._db() / api.conn()
    # 全部落在副本上，绝不会有任何模块旁路连回主库。
    os.environ["NEUROMEM_DB"] = str(copy)
    from neuromem import api, db, identity

    # 2) 副本上真实走一遍 v11→v12 迁移
    c = db.connect(str(copy))
    ver = c.execute("PRAGMA user_version").fetchone()[0]
    rows = c.execute("SELECT subject,COUNT(*) n FROM events GROUP BY subject").fetchall()
    gist_after = [r[0] for r in c.execute("SELECT gist FROM events ORDER BY id")]
    vec_after = c.execute(
        "SELECT COUNT(*) FROM events WHERE embedding IS NOT NULL").fetchone()[0]
    checks = {
        "副本升级到 v12": ver == 12,
        "存量记忆全标 counterpart": bool(rows) and all(r["subject"] == "counterpart" for r in rows),
        "gist 一字未改": gist_before == gist_after,
        "向量无损": vec_before == vec_after and vec_after > 0,
    }
    print("副本·迁移后：user_version=%s，主体分布=%s" % (
        ver, {r["subject"]: r["n"] for r in rows}))

    # 3) L0：拒绝非人工写入、允许人工版本化、校验通过
    ok_bad = not identity.set_profile(c, {"who": "偷偷改"}, actor="llm")["ok"]
    v1 = identity.get(c)["version"]
    up = identity.set_profile(c, {"tone": "简短、温和、先接住情绪"}, actor="human")
    ok_human = up["ok"] and identity.get(c)["version"] == v1 + 1
    ok_verify = identity.verify(c)[0]
    checks["非人工 actor 被拒"] = ok_bad
    checks["人工版本化升级"] = ok_human
    checks["固定身份 checksum 校验"] = ok_verify

    # 4) 清库豁免：api.conn 指向副本
    api.conn = lambda: db.connect(str(copy))
    plan = api.reset_plan("all")
    r = api.reset_all({"confirm": "RESET"})
    c2 = db.connect(str(copy))
    after = _counts(c2)
    checks["清库预检列出身份豁免"] = ("identity" in plan["will_keep"]
                                  and "identity_history" in plan["will_keep"])
    checks["清库成功且事件清空"] = bool(r.get("ok")) and after.get("events") == 0
    checks["清库后固定身份仍在"] = (identity.get(c2)["version"] == v1 + 1
                                and identity.verify(c2)[0])
    checks["清库后版本链仍在"] = after.get("identity_history", 0) >= 1
    print("副本·清库：%d 张表被清，identity v%s 保留，identity_history %s 行" % (
        len(r.get("cleared") or []), identity.get(c2)["version"],
        after.get("identity_history")))
    c2.close()
    c.close()

    # 5) MCP 真实 stdio 会话（指向已清库的副本）
    checks.update(_mcp(copy, full=full))

    # 6) 主库只读复核
    src2 = _ro(SRC)
    main_after = _counts(src2)
    ver_main = src2.execute("PRAGMA user_version").fetchone()[0]
    src2.close()
    diff = {k: (main_before.get(k, 0), main_after.get(k, 0))
            for k in set(main_before) | set(main_after)
            if main_before.get(k, 0) != main_after.get(k, 0)}
    checks["主库 schema 版本未被本脚本改动"] = ver_main == ver_before
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
