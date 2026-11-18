"""阿澈桌面端 —— 陪伴对话的单窗口桌面壳。

设计（M1）：
- 与内核同进程：用线程在原进程内起 FastAPI(neuromem.api.app)，避免历史上
  service.py 后台进程被回收的坑；也保证 live 通道的单写者锁在同一个进程里。
- 免登录：本机桌面窗口不该弹登录。用一个只在本进程生效的 ASGI 中间件给
  每个请求注入合法会话 Cookie（account.make_token 签名），不改内核 auth.py，
  web 服务鉴权行为完全不变。
- 前端：desktop/web/ 整目录挂到 /desk/，页面与 assets 用相对路径（设计稿原样）。
  index.html = 对话页，settings.html = 人设设置，logic.js = 接口层。
- 主动关心：前端轮询 /api/proactive 显示气泡；系统通知通过 js_api 的
  notify() 桥接（Windows 用 PowerShell toast，失败静默降级为窗口内气泡）。

启动：
    python desktop/app.py
或双击 start_desktop.bat
"""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# 让脚本从任意目录启动都能 import 到 neuromem / agent
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FRONTEND = Path(__file__).resolve().parent / "web" / "index.html"
HOST = "127.0.0.1"
PORT = int(os.environ.get("NEUROMEM_DESKTOP_PORT", "8765"))
OPEN_WEB_CMD = f"http://{HOST}:{PORT}/"          # 网页后台地址（web 首页）
TITLE = "阿澈"


def _find_free_port(preferred=PORT):
    """首选端口被占用时顺延找空闲端口，避免和 web 服务/其它程序冲突。"""
    import socket
    for p in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, p))
                return p
            except OSError:
                continue
    return preferred


_WEB_LOGIN_TOKEN = {"value": ""}


class _DesktopAuth:
    """给所有请求注入合法 Cookie，桌面窗口免登录。

    只包在本进程的 ASGI 链路外层，不修改 api.app 本身，也不影响 web 服务。
    """

    def __init__(self, app, token):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            from starlette.datastructures import MutableHeaders
            headers = MutableHeaders(scope=scope)
            cookie = headers.get("cookie", "")
            # 已有同名 cookie 就不覆盖
            if "nm_session=" not in cookie:
                headers["cookie"] = (cookie + "; " if cookie else "") + \
                    f"nm_session={self.token}"
        await self.app(scope, receive, send)


def _build_server(port):
    """构造 uvicorn Server：桌面前端路由 + 鉴权注入中间件。"""
    import uvicorn
    from neuromem.api import app as api_app
    from neuromem import account

    # 桌面端整目录挂载：/desk/ → index.html，其余页面与 assets 相对路径自然成立。
    # 不再用单文件路由：设计稿是多页面（对话 / 设置），相对引用 "assets/x.css"
    # 只有在目录路径下才解析得到。访问 /desk 会由 Starlette 自动 307 到 /desk/。
    # / 仍然保留给网页后台，两边不互相影响。
    from fastapi.staticfiles import StaticFiles
    if not any(getattr(r, "path", "") == "/desk" for r in api_app.routes):
        api_app.mount("/desk", StaticFiles(directory=str(FRONTEND.parent), html=True),
                      name="desk")

    # read_token 会校验用户名与 account.json 一致，必须用真实账号名签。
    # 桌面是本机单用户，用账号里的用户名即可（读不到则留空，让 web 弹登录）。
    acc = account.load() or {}
    username = acc.get("username") or ""
    token = account.make_token(username) if username else ""
    _WEB_LOGIN_TOKEN["value"] = token

    from fastapi.responses import RedirectResponse

    # 后台标签白名单：深链只允许跳这些页，避免把 query 拼进跳转造成怪地址
    _WEB_TABS = {"overview", "events", "concepts", "personas", "relations",
                 "graph", "growth", "chat", "models", "persona", "data", "support"}

    @api_app.get("/desk-web-login")
    def _desk_web_login(t: str = "", tab: str = ""):
        """外部浏览器打开网页后台时，用一次性 token 设置会话再跳转。

        token 由桌面端本地生成、仅本机可达，等价一次静默登录。
        tab 用于深链到指定标签（如「记忆」页），不在白名单内就回概览。
        """
        target = "/?tab=" + tab if tab in _WEB_TABS else "/"
        if not t or t != _WEB_LOGIN_TOKEN["value"]:
            return RedirectResponse("/")
        resp = RedirectResponse(target)
        resp.set_cookie(account.COOKIE, t, max_age=account.TTL,
                        httponly=True, samesite="lax", path="/")
        return resp

    @api_app.get("/api/desktop/meta")
    def _desktop_meta():
        return {
            "ok": True,
            "name": TITLE,
            "web_url": f"http://{HOST}:{port}/desk-web-login?t={_WEB_LOGIN_TOKEN['value']}",
        }

    # 桌面端专用的历史回填接口：只读 conversation_turns，供启动时恢复对话气泡。
    # 只在本进程注册，不进内核 api.py（内核服务不需要、也不暴露它）。
    @api_app.get("/api/live/history")
    def _desk_live_history(limit: int = 60):
        from neuromem import db, companion as comp
        limit = max(1, min(int(limit or 60), 300))
        agent_name = getattr(comp, "AGENT_NAME", None) or "agent"
        c = db.connect()
        try:
            rows = c.execute(
                "SELECT speaker, text, ts FROM conversation_turns "
                "WHERE session_id='live_web' AND text IS NOT NULL AND text<>'' "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
            out = []
            for r in reversed(rows):
                sp = r["speaker"] or ""
                out.append({
                    "role": "agent" if sp == agent_name else "user",
                    "text": r["text"],
                    "speaker": sp,
                    "ts": r["ts"] or "",
                })
            return {"ok": True, "turns": out, "count": len(out)}
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]),
                    "turns": [], "count": 0}
        finally:
            c.close()

    wrapped = _DesktopAuth(api_app, token)

    config = uvicorn.Config(
        wrapped, host=HOST, port=port, log_level="warning",
        access_log=False, lifespan="on",
    )
    return uvicorn.Server(config)


def _toast(title, message):
    """Windows 系统通知；失败静默（窗口内气泡始终可用）。"""
    try:
        import subprocess
        title_esc = str(title).replace("'", "''")
        msg_esc = str(message).replace("'", "''")[:200]
        ps = (f"Add-Type -AssemblyName System.Windows.Forms;"
              f"$n=New-Object System.Windows.Forms.NotifyIcon;"
              f"$n.Icon=[System.Drawing.SystemIcons]::Information;"
              f"$n.BalloonTipTitle='{title_esc}';"
              f"$n.BalloonTipText='{msg_esc}';"
              f"$n.Visible=$true;$n.ShowBalloonTip(5000);"
              f"Start-Sleep -Milliseconds 5200;$n.Dispose()")
        subprocess.Popen(["powershell", "-NoProfile", "-Command", ps],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


class _Api:
    """暴露给前端 JS 的桥接方法（pywebview js_api）。"""

    def notify(self, title, message):
        _toast(title or TITLE, message or "")

    def open_web(self, tab=""):
        try:
            t = _WEB_LOGIN_TOKEN["value"]
            url = OPEN_WEB_CMD if not t else \
                OPEN_WEB_CMD.rstrip("/") + "/desk-web-login?t=" + t
            if tab:
                url += "&tab=" + str(tab)
            webbrowser.open(url)
        except Exception:
            pass

    def quit(self):
        try:
            import webview
            webview.destroy_window()
        except Exception:
            pass


def main():
    import webview

    port = _find_free_port()
    global OPEN_WEB_CMD
    OPEN_WEB_CMD = f"http://{HOST}:{port}/"

    server = _build_server(port)
    t = threading.Thread(target=server.run, name="neuromem-api", daemon=True)
    t.start()

    # 等服务真正起来（最多 ~15 秒）
    import urllib.request
    ready = False
    for _ in range(60):
        if not server.started:
            time.sleep(0.25)
            continue
        try:
            urllib.request.urlopen(f"http://{HOST}:{port}/api/desktop/meta",
                                   timeout=2).read()
            ready = True
            break
        except Exception:
            time.sleep(0.25)
    if not ready:
        print(f"[阿澈] 服务未能就绪（端口 {port}），请查看控制台错误。", file=sys.stderr)

    api = _Api()
    window = webview.create_window(
        TITLE,
        f"http://{HOST}:{port}/desk/",
        js_api=api,
        width=420, height=760,
        min_size=(360, 560),
        resizable=True,
        background_color="#1a1a1a",
        confirm_close=False,
    )
    # 关窗后停服务、退出进程
    def _on_closed():
        try:
            server.should_exit = True
        except Exception:
            pass
    try:
        window.events.closed += _on_closed
    except Exception:
        pass

    webview.start(debug=False)
    server.should_exit = True


if __name__ == "__main__":
    main()
