"""端到端全流程验收：登录 → 写入 → 检索 → 回答 → 进化 → 成长 → 管理 → 导出。

用临时管理员账号，跑完恢复原账号文件，不影响你自己的密码。

用法：
    python tests/e2e_flow.py
"""
import http.cookiejar
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8000"
DATA = r"D:\neuro-memory\data"
ACC = os.path.join(DATA, "account.json")
KEY = os.path.join(DATA, "session.key")
BAK = ACC + ".e2e-bak"
BAK_KEY = KEY + ".e2e-bak"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s %-28s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:70]))


def main():
    restored = False
    try:
        # 自愈：上次异常中断留下的备份，先还原（否则会把你的账号一起删掉）
        for f in (ACC, KEY):
            if os.path.exists(f + ".e2e-bak"):
                try:
                    os.remove(f)
                except OSError:
                    pass
                shutil.move(f + ".e2e-bak", f)
                print("自愈：还原上次中断留下的账号备份")

        for f in (ACC, KEY):
            bak = f + ".e2e-bak"
            # 只在没有备份时才复制：避免把测试账号覆盖到原备份上
            if os.path.exists(f) and not os.path.exists(bak):
                shutil.copy2(f, bak)
        for f in (ACC, KEY):
            try:
                os.remove(f)
            except OSError:
                pass

        sys.path.insert(0, r"D:\neuro-memory")
        from neuromem import account
        account.create("e2e_admin", "e2e_pass_123")

        cj = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

        def call(path, method="GET", body=None, timeout=240):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
            req = urllib.request.Request(BASE + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
            try:
                with op.open(req, timeout=timeout) as r:
                    raw = r.read().decode("utf-8")
                    return r.status, (json.loads(raw) if raw else {})
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8")
                try:
                    return e.code, json.loads(raw or "{}")
                except Exception:
                    return e.code, {}

        # 1. 登录前
        st, d = call("/api/auth/me")
        check("登录前 /auth/me", st == 200 and d.get("initialized") is True, d)
        st, d = call("/api/overview")
        check("未登录拦截 401", st == 401, st)

        # 2. 登录
        st, d = call("/api/auth/login", "POST", {"username": "e2e_admin", "password": "e2e_pass_123"})
        check("登录", st == 200 and d.get("ok"), d)
        st, d = call("/api/auth/me")
        check("登录后 /auth/me", d.get("logged_in") is True, d.get("username"))

        # 3. 只读接口
        st, ov = call("/api/overview")
        check("概览", st == 200 and "active_events" in ov, ov.get("active_events"))
        for path in ("/api/events", "/api/concepts", "/api/personas", "/api/relations",
                     "/api/graph", "/api/growth", "/api/meta", "/api/self", "/api/usage?days=1"):
            st, d = call(path)
            check("GET " + path.split("?")[0], st == 200, st)

        # 4. 写入
        before = ov.get("active_events", 0)
        text = "今天在测试端到端流程，我把所有链路都走了一遍。"
        st, d = call("/api/events", "POST", {"text": text, "speaker": "用户"})
        eid = (d.get("ids") or [d.get("id")])[0] if isinstance(d, dict) else None
        check("写入记忆", st == 200 and eid, eid)

        st, rows = call("/api/events?limit=5")
        check("写入后可查", any(text[:8] in (r.get("gist") or "") for r in rows) if isinstance(rows, list) else False)

        # 5. 检索 + 回答
        st, d = call("/api/ask", "POST", {"query": "端到端流程"})
        check("检索有结果", st == 200 and len(d.get("events") or []) > 0, len(d.get("events") or []))
        check("生成回答", bool((d.get("summary") or "").strip()), (d.get("summary") or "")[:40])

        st, d = call("/api/retrieve", "POST", {"query": "端到端流程", "top_k": 5})
        check("retrieve 接口", st == 200 and d.get("ok") and d.get("events"),
              (d.get("timings") or {}).get("recall_ms"))

        # 目标：新增 → 列表 → 完成 → 删除
        st, d = call("/api/goals", "POST", {"text": "端到端验收目标"})
        gid = d.get("id")
        check("新增目标", st == 200 and d.get("ok") and gid, gid)
        st, g = call("/api/goals")
        check("目标列表", st == 200 and any(x["id"] == gid for x in (g.get("active") or [])))
        st, d = call("/api/goals/%s/done" % gid, "POST")
        check("完成目标", st == 200 and d.get("ok"))
        st, d = call("/api/goals/%s" % gid, "DELETE")
        check("删除目标", st == 200 and d.get("ok"))

        # 6. 编辑 / 归档 / 取消归档
        if eid:
            st, d = call("/api/events/%s" % eid, "PUT", {"gist": "端到端流程验收记录", "place": "本地"})
            check("编辑事件", st == 200 and d.get("ok", True), d.get("ok"))
            st, d = call("/api/events/%s/archive?archived=1" % eid, "POST")
            check("归档事件", st == 200 and d.get("ok", True), d.get("ok"))
            st, d = call("/api/events/%s/archive?archived=0" % eid, "POST")
            check("取消归档", st == 200 and d.get("ok", True), d.get("ok"))

        # 7. 手动进化
        t0 = time.time()
        st, d = call("/api/evolve", "POST", {}, timeout=600)
        check("手动进化", st == 200 and (d.get("ok") or d.get("skipped")), "%s %.1fs" % (d.get("new_concepts", "-"), time.time() - t0))
        st, d = call("/api/evolve/status")
        check("进化状态", st == 200 and d.get("last_run"), d.get("last_run"))

        # 8. 配置与用量
        st, d = call("/api/settings")
        check("读取配置", st == 200 and d.get("settings"), (d.get("settings") or {}).get("llm", {}).get("provider"))
        st, d = call("/api/settings/test", "POST", {"role": "embed"}, timeout=120)
        check("嵌入连通性", st == 200 and d.get("ok"), d.get("error", ""))
        st, u = call("/api/usage?days=1")
        check("用量统计", st == 200 and (u.get("totals") or {}).get("calls", 0) > 0,
              (u.get("totals") or {}).get("calls"))

        # 9. 导出
        st, ex = call("/api/export")
        check("导出", st == 200 and len(ex.get("events") or []) > 0, len(ex.get("events") or []))

        # 10. 清理测试事件
        if eid:
            st, d = call("/api/events/%s" % eid, "DELETE")
            check("删除测试事件", st == 200 and d.get("ok", True), d.get("ok"))

        st, ov2 = call("/api/overview")
        check("数据回到写入前", ov2.get("active_events") == before,
              "%s→%s" % (before, ov2.get("active_events")))

        # 11. 登出
        st, d = call("/api/auth/logout", "POST")
        check("登出", st == 200 and d.get("ok"))
        st, d = call("/api/overview")
        check("登出后拦截", st == 401, st)

    finally:
        for f in (ACC, KEY):
            bak = f + ".e2e-bak"
            try:
                os.remove(f)  # 先删测试账号
            except OSError:
                pass
            if os.path.exists(bak):
                shutil.move(bak, f)
                restored = True
        if not restored:
            print("\n原本没有账号：测试账号已清除，请在页面重新创建")
        else:
            print("\n账号文件已恢复：是")

    passed = sum(1 for r in results if r[1])
    print("\n合计 %d/%d 通过" % (passed, len(results)))
    failed = [r[0] for r in results if not r[1]]
    if failed:
        print("失败项：" + "、".join(failed))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())