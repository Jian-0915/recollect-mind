"""访问控制：管理员账号 + 签名会话 Cookie。

- 首页与静态资源放行（页面自己负责弹登录框）
- `/api/auth/*` 与 `/api/health` 放行
- 其余 `/api/*` 需要有效的 `nm_session` Cookie

账号管理见 `account.py`；忘记密码用 `python service.py passwd`。
"""
from . import account

PUBLIC_API = {
    "/api/health",
    "/api/auth/me",
    "/api/auth/login",
    "/api/auth/setup",
    "/api/auth/logout",
}


def current_user(request):
    return account.read_token(request.cookies.get(account.COOKIE))


def allowed(request):
    path = request.url.path or "/"
    if path in ("/", "/index.html") or path.startswith("/static") or path.startswith("/favicon"):
        return True
    if path in PUBLIC_API:
        return True
    return current_user(request) is not None