"""念兹桌面端 —— 陪伴对话的单窗口桌面壳。

品牌名「念兹」：念兹在兹，念念不忘于此。一个"念"字盖住记忆、惦记、念出来。
界面里的角色名（微微 / 傻包 …）由人设卡决定，与品牌名是两层，互不影响。

设计（M1）：
- 与内核同进程：用线程在原进程内起 FastAPI(neuromem.api.app)，避免历史上
  service.py 后台进程被回收的坑；也保证 live 通道的单写者锁在同一个进程里。
- 免登录：本机桌面窗口不该弹登录。用一个只在本进程生效的 ASGI 中间件给
  每个请求注入合法会话 Cookie（account.make_token 签名），不改内核 auth.py，
  web 服务鉴权行为完全不变。
- 前端：desktop/web/ 整目录挂到 /desk/，页面与 assets 用相对路径（设计稿原样）。
  index.html = 对话页，settings.html = 人设设置，logic.js = 接口层。
- 主动关心：前端轮询 /api/proactive 显示气泡；系统通知走 js_api 的
  notify()（Windows 原生可点击 Toast，点击回到聊天页；失败降级气球）。
- 语音（V6）：桌面壳拉起本地 tts-server 常驻服务（desktop/tts.py），
  /api/tts/status 报可用性、/api/tts 流式转发合成音频给前端播放。
  手动触发，不自动念；缺模型/服务失败时按钮隐藏，不影响对话。
- 回复通知：文字气泡与语音缓存都就绪后，前端调 notify_reply() 发系统
  通知（像微信来消息），点击回聊天页；语音不可用时降级为文字即通知。

启动（Windows）：
    双击 start_desktop.bat        —— 无控制台，日志写入 data/desktop.log
    双击 start_desktop_debug.bat  —— 保留控制台，排错时用
也可：python desktop/app.py
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

# pythonw.exe（无控制台）下 sys.stdout / sys.stderr 是 None，任何
# print(file=sys.stderr) 都会抛 AttributeError，uvicorn 内部也要往 stderr 写。
# 这里统一兜底到日志文件（默认 data/desktop.log，可用 NEUROMEM_DESKTOP_LOG 改），
# 保留出错可查的能力。
# 必须在 import uvicorn / webview 之前完成（它们在 main() 里才 import）。
if sys.stdout is None or sys.stderr is None:
    try:
        _log_env = os.environ.get("NEUROMEM_DESKTOP_LOG")
        _log_path = Path(_log_env) if _log_env else ROOT / "data" / "desktop.log"
        _log_path.parent.mkdir(parents=True, exist_ok=True)
        _log_fp = open(_log_path, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _log_fp
        if sys.stderr is None:
            sys.stderr = _log_fp
    except Exception:
        pass                      # 日志兜底失败也不能挡住窗口启动

FRONTEND = Path(__file__).resolve().parent / "web" / "index.html"
HOST = "127.0.0.1"
PORT = int(os.environ.get("NEUROMEM_DESKTOP_PORT", "8765"))
OPEN_WEB_CMD = f"http://{HOST}:{PORT}/"          # 网页后台地址（web 首页）
BRAND = "念兹"                  # 品牌名：念兹在兹，念念不忘于此
PRODUCT_TITLE = BRAND           # 窗口标题栏：固定品牌名，不跟人设改名而变
TITLE = BRAND                   # 通知来源兜底：前端没给角色名时才用


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

# 系统通知点击回跳用的窗口引用。必须放在**模块级**而绝不能塞进 _Api 实例：
# pywebview 对 js_api 做 dir() 递归反射，任何不可调用且带 __module__ 的属性
# （如 webview.Window 实例）都会被无限展开，导致窗口卡死在「未响应」。
# 模块级私有变量不在实例上，桥接反射看不到，回调时再从这里取。
_WINDOW_REF = {"window": None, "chat_url": ""}


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

        # 桌面端静态资源禁止缓存：WebView2 会强缓存 js/css，改了前端不生效，
        # 用户得手动清缓存或重装。加 no-cache 后每次启动都拉最新文件。
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if path.startswith("/desk/"):
            async def _send(message):
                if message["type"] == "http.response.start":
                    h = MutableHeaders(scope=message)
                    h["cache-control"] = "no-cache, no-store, must-revalidate"
                    h["pragma"] = "no-cache"
                await send(message)
            await self.app(scope, receive, _send)
        else:
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
    _WEB_TABS = {"overview", "events", "knowledge", "concepts", "personas", "relations",
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
        from desktop import tts
        return {
            "ok": True,
            "name": PRODUCT_TITLE,
            "web_url": f"http://{HOST}:{port}/desk-web-login?t={_WEB_LOGIN_TOKEN['value']}",
            "tts": tts.status(),
        }

    # ---------------- 语音合成（本地神经 TTS） ----------------
    # 默认手动触发：前端「念出来」按钮才调，不自动朗读每一句。
    # 桌面壳做中转而非前端直连 tts-server：音色白名单、文本长度、播控状态
    # 都收在一处，也不把本地服务端口暴露给 webview。

    @api_app.get("/api/tts/status")
    def _tts_status():
        from desktop import tts
        return tts.status()

    @api_app.post("/api/tts/start")
    def _tts_start():
        """供前端首次点播时按需拉起服务（同步等待就绪，约 2~4 秒）。"""
        from desktop import tts
        ok = tts.start()
        st = tts.status()
        st["ok"] = ok
        if not ok:
            st["error"] = st.get("reason") or "语音服务启动失败"
        return st

    _MAX_TTS_TEXT = 400   # 单句朗读上限，防止一次吐太长卡住合成

    @api_app.post("/api/tts")
    def _tts_speak(payload: dict):
        """把文本交给本地 tts-server，流式转发 s16le PCM（24k 单声道）。

        返回 content-type: audio/pcm 的流式响应；前端边收边播。合成失败返回
        JSON {ok:false,error}，前端据此隐藏按钮或提示，绝不吞掉文字回复。
        """
        import requests
        from fastapi.responses import StreamingResponse, JSONResponse
        from desktop import tts

        text = str((payload or {}).get("text") or "").strip()
        voice = str((payload or {}).get("voice") or tts.DEFAULT_VOICE).strip()

        if not text:
            return JSONResponse({"ok": False, "error": "没有要念的内容"}, status_code=400)
        if len(text) > _MAX_TTS_TEXT:
            text = text[:_MAX_TTS_TEXT]
        # 音色严格白名单：只接受服务内置的那几个名字，杜绝把外部字符串
        # 拼进下游 JSON 或日志造成注入。
        if voice not in tts.VOICES:
            voice = tts.DEFAULT_VOICE

        if not tts.is_ready():
            # 按需兜底拉起：万一用户没等后台线程就绪就点了按钮
            if not tts.start():
                st = tts.status()
                return JSONResponse(
                    {"ok": False, "error": st.get("reason") or "语音服务未就绪"},
                    status_code=503,
                )

        body = {
            "input": text,
            "voice": voice,
            "language": "Chinese",
            "response_format": "pcm",
            "seed": 42,
        }
        try:
            upstream = requests.post(
                tts.BASE + "/v1/audio/speech", json=body, stream=True, timeout=(5, 90),
            )
        except Exception as e:
            return JSONResponse(
                {"ok": False, "error": "合成请求失败：%s" % type(e).__name__},
                status_code=502,
            )
        if upstream.status_code != 200:
            detail = upstream.text[:200] if upstream.text else "HTTP %s" % upstream.status_code
            upstream.close()
            return JSONResponse({"ok": False, "error": "合成失败：" + detail}, status_code=502)

        def _stream():
            try:
                for chunk in upstream.iter_content(chunk_size=4096):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        return StreamingResponse(
            _stream(), media_type="audio/pcm",
            headers={"X-TTS-Voice": voice, "Cache-Control": "no-store"},
        )

    @api_app.post("/api/tts/precache")
    def _tts_precache(payload: dict):
        """回复产生时调用：把这句交给后台队列预合成，立即返回缓存 key。

        不阻塞对话。key 由音色+文本决定；同一句重复入队会被去重。
        返回 {ok, key}；服务不可用/文本空时 ok=False，上层静默降级。
        """
        from desktop import tts
        text = str((payload or {}).get("text") or "").strip()
        voice = str((payload or {}).get("voice") or tts.DEFAULT_VOICE).strip()
        ok, _ = tts.available()
        key = tts.precache(text, voice) if ok else ""
        return {"ok": bool(key), "key": key}

    @api_app.post("/api/tts/ensure")
    def _tts_ensure(payload: dict):
        """点播时调用：入队并等缓存就绪（最多 ~20 秒），就绪后前端 <audio> 直读。

        正常情况回复产生时已预热好，这里秒回；排队中/首次合成时按钮会显示
        "准备中"。超时返回 ok=False，前端提示，绝不吞掉文字对话。
        """
        import time
        from desktop import tts
        text = str((payload or {}).get("text") or "").strip()
        voice = str((payload or {}).get("voice") or tts.DEFAULT_VOICE).strip()
        ok, _ = tts.available()
        key = tts.precache(text, voice) if ok else ""
        if not key:
            return {"ok": False, "error": "语音服务不可用"}
        end = time.time() + 20
        while time.time() < end:
            if tts.has_cache(key):
                return {"ok": True, "key": key}
            time.sleep(0.25)
        return {"ok": False, "key": key, "error": "语音合成超时"}

    @api_app.get("/api/tts/audio/{key}")
    def _tts_audio(key: str):
        """直读预合成缓存的 wav。key 严格 20 位 hex，防路径穿越。

        命中返回 audio/wav（浏览器 <audio> 原生播，零等待）；
        未命中返回 404，前端据此走兜底实时合成。
        """
        from fastapi.responses import FileResponse, JSONResponse
        from desktop import tts
        if not tts.has_cache(key):
            return JSONResponse({"ok": False, "error": "语音尚未就绪"}, status_code=404)
        return FileResponse(
            tts._cache_file(key), media_type="audio/wav",
            headers={"Cache-Control": "no-store", "X-TTS-Cache": "hit"},
        )

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

    # 桌面端专用：外部知识答问（独立库，不碰 events/concepts）。
    # 只在本进程注册。同步执行（联网+生成约十几秒），前端用 async fetch
    # 调用不阻塞 UI；js_api 那条同步桥接不碰它。
    @api_app.post("/api/research")
    async def _desk_research(request: Request):
        from neuromem import research as rmod
        try:
            data = await request.json()
            text = str((data or {}).get("text") or "")
            force = bool((data or {}).get("force"))
        except Exception:
            text, force = "", False
        try:
            from neuromem import db as _db, research as rmod
            main_c = _db.connect()
            try:
                private = rmod.private_terms_from_db(main_c)
            finally:
                main_c.close()
            r = rmod.research(text, private_terms=private, force=force)
            return {"ok": True, **r.to_dict()}
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]),
                    "answer": "", "sources": []}

    @api_app.get("/api/knowledge")
    def _desk_knowledge(limit: int = 30):
        from neuromem import knowledge as kmod
        c = None
        try:
            c = kmod.connect()
            return {"ok": True, "items": [
                {"title": h.title, "url": h.url, "snippet": h.snippet,
                 "fetched_at": h.fetched_at, "provider": h.provider}
                for h in kmod.list_recent(c, min(max(limit, 1), 200))],
                **kmod.stats(c)}
        except Exception as e:
            return {"ok": False, "error": str(e), "items": []}
        finally:
            if c is not None:
                c.close()

    @api_app.get("/api/knowledge/search")
    def _desk_knowledge_search(q: str = "", limit: int = 8):
        from neuromem import knowledge as kmod
        c = None
        try:
            c = kmod.connect()
            hits = kmod.search(c, q, top_k=min(max(limit, 1), 30))
            return {"ok": True, "query": q, "items": [
                {"title": h.title, "url": h.url, "snippet": h.snippet,
                 "fetched_at": h.fetched_at, "score": h.score} for h in hits]}
        except Exception as e:
            return {"ok": False, "error": str(e), "items": []}
        finally:
            if c is not None:
                c.close()

    # F5 自主外部获取：状态 / 审计日志 / 开关
    @api_app.get("/api/autoresearch/status")
    def _desk_autoresearch_status():
        from neuromem import autoresearch as ar
        return {"ok": True, **ar.status()}

    @api_app.get("/api/autoresearch/log")
    def _desk_autoresearch_log(limit: int = 30):
        from neuromem import knowledge as kmod
        c = None
        try:
            c = kmod.connect()
            return {"ok": True, "items": kmod.list_autoresearch(
                c, min(max(limit, 1), 200))}
        except Exception as e:
            return {"ok": False, "error": str(e), "items": []}
        finally:
            if c is not None:
                c.close()

    @api_app.get("/api/autoresearch/brief")
    def _desk_autoresearch_brief():
        """知识播报：取一条她自己查到、模型整理好的短句。GET 即消费。"""
        from neuromem import knowledge as kmod
        c = None
        try:
            c = kmod.connect()
            item = kmod.peek_brief(c)
            if not item:
                return {"ok": True, "available": False}
            return {"ok": True, "available": True, "text": item.get("text", ""),
                    "entity": item.get("entity", ""), "sources": item.get("sources", []),
                    "created_at": item.get("created_at", "")}
        except Exception as e:
            return {"ok": False, "available": False,
                    "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
        finally:
            if c is not None:
                c.close()

    @api_app.post("/api/autoresearch/toggle")
    async def _desk_autoresearch_toggle(request: Request):
        from neuromem import autoresearch as ar
        try:
            data = await request.json()
            on = bool((data or {}).get("enabled"))
            ar.set_enabled(on)
            return {"ok": True, "enabled": ar.enabled()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # 外部 MCP 服务管理：桌面独立进程也需要（共用 api_app 对象但不共享注册时机）
    @api_app.get("/api/mcp/servers")
    def _desk_mcp_servers():
        from neuromem import settings as _settings
        try:
            items = _settings.mcp_load()
            active = _settings.mcp_active(items)
            return {"ok": True, "servers": _settings.mcp_public(items),
                    "active_id": (active or {}).get("id")}
        except Exception as e:
            return {"ok": False, "error": str(e), "servers": [], "active_id": None}

    @api_app.put("/api/mcp/servers")
    async def _desk_mcp_save(request: Request):
        from neuromem import settings as _settings
        try:
            payload = await request.json()
            items = (payload or {}).get("servers")
            if not isinstance(items, list):
                return {"ok": False, "error": "servers 必须是数组"}
            old = {s.get("id"): s for s in _settings.mcp_load()}
            merged = []
            for raw in items:
                row = dict(raw or {})
                key = row.get("api_key")
                rid = row.get("id")
                if (key is None or (isinstance(key, str)
                                    and (not key.strip() or key.startswith("****")))):
                    row["api_key"] = (old.get(rid) or {}).get("api_key", "")
                merged.append(row)
            saved = _settings.mcp_save(merged)
            active = _settings.mcp_active(saved)
            return {"ok": True, "servers": _settings.mcp_public(saved),
                    "active_id": (active or {}).get("id")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @api_app.post("/api/mcp/test")
    async def _desk_mcp_test(request: Request):
        from neuromem import settings as _settings, tools as _tools
        import time as _time
        t0 = _time.time()
        try:
            p = await request.json()
            p = p or {}
            sid = p.get("id")
            if sid:
                srv = next((s for s in _settings.mcp_load()
                            if s.get("id") == sid), None)
                if not srv:
                    return {"ok": False, "error": "找不到该服务"}
                for f in ("url", "api_key", "tool"):
                    if f in p:
                        srv[f] = p[f]
                if isinstance(srv.get("api_key"), str) and srv["api_key"].startswith("****"):
                    srv["api_key"] = next((s for s in _settings.mcp_load()
                                           if s.get("id") == sid), {}).get("api_key", "")
            else:
                srv = {"url": p.get("url") or "", "api_key": p.get("api_key") or "",
                       "tool": p.get("tool") or "search"}
            res = _tools.provider_from_server(srv).test_connection()
            res["latency"] = round(_time.time() - t0, 2)
            return {"ok": True, **res}
        except Exception as e:
            return {"ok": False, "latency": round(_time.time() - t0, 2),
                    "error": "%s: %s" % (type(e).__name__, str(e)[:300])}

    @api_app.post("/api/mcp/active")
    async def _desk_mcp_active(request: Request):
        from neuromem import settings as _settings
        try:
            payload = await request.json()
            sid = str((payload or {}).get("id") or "")
            items = _settings.mcp_load()
            if not any(s.get("id") == sid for s in items):
                return {"ok": False, "error": "找不到该服务"}
            for s in items:
                s["enabled"] = (s.get("id") == sid)
            saved = _settings.mcp_save(items)
            return {"ok": True, "servers": _settings.mcp_public(saved), "active_id": sid}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    wrapped = _DesktopAuth(api_app, token)

    config = uvicorn.Config(
        wrapped, host=HOST, port=port, log_level="warning",
        access_log=False, lifespan="on",
    )
    return uvicorn.Server(config)


def _toast(title, message):
    """兼容旧调用：只发不可点击的气球提示。新通知走 desktop.notify.send。"""
    from desktop import notify
    return notify._balloon(title, message)


class _Api:
    """暴露给前端 JS 的桥接方法（pywebview js_api）。"""

    def __init__(self, port=None):
        # 不在这里存 window / chat_url 等任何复杂对象属性——pywebview 会
        # dir() 递归反射整个实例，见模块级 _WINDOW_REF 的说明。
        pass

    def notify(self, title, message):
        """系统通知（主动关心等场景）：可点击，点了回到聊天页。"""
        from desktop import notify
        notify.send(title or TITLE, message or "", self._focus_chat)

    def notify_reply(self, title, message):
        """她的回复就绪后由前端调用：系统通知，点击直接回聊天页。

        与 notify() 分开命名，是为了让前端明确「这条是等语音和文字都
        准备好之后才发的」，也让日后能给两类通知不同的点击行为。
        """
        from desktop import notify
        notify.send(title or TITLE, message or "", self._focus_chat)

    @staticmethod
    def _focus_chat():
        """系统通知被点击：恢复窗口并回到聊天页，聚焦输入框。

        静态方法 + 模块级窗口引用：既不把 Window 暴露给 js_api 反射，
        又能让 notify 的回调在任意线程安全拿到窗口。
        """
        w = _WINDOW_REF["window"]
        if w is None:
            return
        try:
            w.restore()
        except Exception:
            pass
        try:
            w.show()
        except Exception:
            pass
        chat_url = _WINDOW_REF["chat_url"]
        try:
            # 已在聊天页：只把光标放回输入框、滚到底，不重载页面（保住草稿）。
            on_chat = w.evaluate_js(
                "!!document.getElementById('input') && "
                "!!document.getElementById('chat')"
            )
            if on_chat is True or on_chat == "true":
                w.evaluate_js(
                    "var i=document.getElementById('input');"
                    "if(i){i.focus();}"
                    "var c=document.getElementById('chat');"
                    "if(c){c.scrollTop=c.scrollHeight;}"
                )
            else:
                w.load_url(chat_url + "?from=notify")
        except Exception:
            try:
                w.load_url(chat_url + "?from=notify")
            except Exception:
                pass

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
        print(f"[{BRAND}] 服务未能就绪（端口 {port}），请查看日志文件 data/desktop.log。", file=sys.stderr)

    # 语音服务后台拉起：模型加载要 2~4 秒，不能挡住窗口出现。
    # 若模型未安装，tts.start() 会秒退，前端 status 显示不可用即可，不影响对话。
    def _tts_boot():
        try:
            from desktop import tts
            if tts.available()[0]:
                tts.start()
        except Exception:
            pass
    threading.Thread(target=_tts_boot, name="tts-boot", daemon=True).start()

    api = _Api(port)
    window = webview.create_window(
        PRODUCT_TITLE,
        f"http://{HOST}:{port}/desk/",
        js_api=api,
        width=420, height=760,
        min_size=(360, 560),
        resizable=True,
        background_color="#1a1a1a",
        confirm_close=False,
    )
    # 通知点击要能操作窗口：写进模块级引用，**绝不**挂到 api 实例上。
    # pywebview 对 js_api 做 dir() 递归反射，Window 实例带 __module__ 会被
    # 无限展开（pty.Empty.Empty... → RecursionError → 窗口卡在未响应）。
    _WINDOW_REF["window"] = window
    _WINDOW_REF["chat_url"] = f"http://{HOST}:{port}/desk/index.html"
    # 关窗后停服务、回收 TTS 子进程、退出进程
    def _on_closed():
        try:
            server.should_exit = True
        except Exception:
            pass
        try:
            from desktop import tts
            tts.stop()
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
