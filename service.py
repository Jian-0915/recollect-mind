"""NeuroMem 服务管理：启动 / 停止 / 重启 / 状态。

用法:
    python service.py start [--port 8000]
    python service.py stop
    python service.py restart
    python service.py status
    python service.py account     # 查看管理员账号状态
    python service.py passwd      # 忘记密码时重置（CLI，需本机权限）

特点:
- PID 记录在 data/service.json，状态可查
- 启动后轮询 /api/health 确认真正就绪
- 停止前校验目标进程是 python，避免 PID 复用导致误杀
- 日志追加到 data/server.log 与 data/server.err.log
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
STATE = DATA / "service.json"
LOG = DATA / "server.log"
ERR = DATA / "server.err.log"

WIN = sys.platform == "win32"


def _out(text=""):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("utf-8", "replace").decode("utf-8", "replace"))


def read_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_state(d):
    DATA.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_state():
    try:
        STATE.unlink()
    except Exception:
        pass


def health(port, timeout=2):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port,
                                    timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def pid_alive(pid):
    if not pid:
        return False
    if WIN:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, errors="ignore").stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pid_is_python(pid):
    if not WIN:
        return True
    out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, errors="ignore").stdout.lower()
    return "python" in out


def port_pid(port):
    """返回监听该端口的 PID（Windows 用 PowerShell，其他平台用 lsof）。"""
    if WIN:
        ps = ("Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue "
              "| Select-Object -First 1 -ExpandProperty OwningProcess" % port)
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, errors="ignore").stdout.strip()
        return int(out) if out.isdigit() else None
    try:
        out = subprocess.run(["lsof", "-ti", "tcp:%d" % port, "-sTCP:LISTEN"],
                             capture_output=True, text=True).stdout.split()
        return int(out[0]) if out else None
    except Exception:
        return None


def start(port=8000, wait=45):
    if health(port):
        _out("已在运行 (pid %s, http://127.0.0.1:%d)" % (port_pid(port) or "-", port))
        return 0

    holder = port_pid(port)
    if holder:
        _out("端口 %d 已被 PID %s 占用（接口无响应）。请先执行 stop 或手动处理。" % (port, holder))
        return 1

    DATA.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "a", encoding="utf-8")
    err = open(ERR, "a", encoding="utf-8")
    flags = 0
    if WIN:
        # DETACHED_PROCESS：脱离启动它的控制台 / 进程组。否则从自动化会话
        # 里执行 start 时，命令一结束子进程会被连带回收（端口随即不通）。
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_NO_WINDOW)
    _out("启动中…（端口 %d）" % port)
    p = subprocess.Popen(
        [sys.executable, "-m", "neuromem", "serve", str(port)],
        cwd=str(BASE), stdout=log, stderr=err, stdin=subprocess.DEVNULL,
        creationflags=flags if WIN else 0,
        start_new_session=(not WIN),
    )
    write_state({"pid": p.pid, "port": port,
                 "started_at": time.strftime("%Y-%m-%d %H:%M:%S")})

    deadline = time.time() + wait
    while time.time() < deadline:
        if health(port):
            _out("已启动 · pid %d · http://127.0.0.1:%d" % (p.pid, port))
            _out("日志: %s" % LOG)
            return 0
        if p.poll() is not None:
            _out("进程启动失败 (exit %s)，请查看：%s" % (p.returncode, ERR))
            clear_state()
            return 1
        time.sleep(0.5)

    _out("等待超时（%ds），服务可能仍在初始化。请查看日志：%s" % (wait, LOG))
    return 1


def stop(port=None, wait=20):
    st = read_state()
    p = st.get("port") or port or 8000
    pid = st.get("pid")

    if not pid or not pid_alive(pid):
        pid = port_pid(p)
        if pid:
            _out("状态文件无有效 PID，改用端口占用进程：%d" % pid)

    if not pid:
        _out("未发现运行中的服务")
        clear_state()
        return 0

    if not pid_is_python(pid):
        _out("拒绝停止：PID %d 不是 python 进程（PID 可能已被系统复用）。请手动检查。" % pid)
        return 1

    _out("停止中…（pid %d）" % pid)
    try:
        if WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, text=True, errors="ignore")
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as e:
        _out("停止失败：%s" % e)
        return 1

    deadline = time.time() + wait
    while time.time() < deadline:
        if not pid_alive(pid) and not health(p):
            break
        time.sleep(0.4)

    clear_state()
    if health(p):
        _out("进程已结束，但端口 %d 仍响应（可能有其他实例）。" % p)
        return 1
    _out("已停止")
    return 0


def status(port=None):
    st = read_state()
    p = st.get("port") or port or 8000
    pid = st.get("pid")
    if not pid or not pid_alive(pid):
        pid = port_pid(p) or pid
    alive = pid_alive(pid) if pid else False
    ok = health(p)

    _out("状态  : %s" % ("运行中" if (alive and ok) else ("未运行" if not alive else "进程在但接口无响应")))
    _out("PID   : %s" % (pid or "-"))
    _out("端口  : %d" % p)
    _out("启动于: %s" % st.get("started_at", "-"))
    _out("地址  : http://127.0.0.1:%d" % p)
    _out("日志  : %s" % LOG)
    if ok:
        try:
            from neuromem import db
            c = db.connect()
            try:
                n = c.execute("SELECT COUNT(*) FROM events WHERE archived=0").fetchone()[0]
                con = c.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
                per = c.execute("SELECT COUNT(*) FROM personas").fetchone()[0]
            finally:
                c.close()
            _out("数据  : %d 事件 · %d 概念 · %d 人物" % (n, con, per))
        except Exception:
            pass
        try:
            from neuromem import autoevolve
            e = autoevolve.status()
            _out("进化  : %s · 每 %ss · 已跑 %s 轮 · 上次 %s"
                 % ("开启" if e.get("enabled") else "关闭", e.get("interval"),
                    e.get("runs"), e.get("last_run") or "-"))
        except Exception:
            pass
    return 0 if (alive and ok) else 2


def main():
    ap = argparse.ArgumentParser(description="NeuroMem 服务管理")
    ap.add_argument("action", nargs="?", default="status",
                    choices=["start", "stop", "restart", "status", "account", "passwd"])
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()

    if a.action == "account":
        from neuromem import account
        if not account.initialized():
            _out("尚未创建管理员账号 —— 打开 http://127.0.0.1:%d 按页面提示创建" % a.port)
        else:
            _out("管理员账号: %s" % ((account.load() or {}).get("username") or "-"))
            _out("页面地址  : http://127.0.0.1:%d" % a.port)
            _out("（用户名 / 密码可在页面左下角「账号」里修改）")
        return 0
    if a.action == "passwd":
        import getpass
        from neuromem import account
        if not account.initialized():
            _out("尚未创建管理员账号，请先在页面初始化")
            return 1
        p1 = getpass.getpass("新密码（至少 6 位）: ")
        p2 = getpass.getpass("再输一次: ")
        if p1 != p2:
            _out("两次输入不一致")
            return 1
        r = account.reset_password(p1)
        if r.get("ok"):
            _out("已重置密码：%s" % r["username"])
            return 0
        _out(r.get("error") or "重置失败")
        return 1

    if a.action == "start":
        return start(a.port)
    if a.action == "stop":
        return stop(a.port)
    if a.action == "restart":
        rc = stop(a.port)
        if rc != 0:
            return rc
        time.sleep(1)
        return start(a.port)
    return status(a.port)


if __name__ == "__main__":
    sys.exit(main())