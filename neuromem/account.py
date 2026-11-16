"""管理员账号：PBKDF2 密码哈希 + HMAC 签名会话 Cookie（无状态，重启不掉线）。

- 账号存 `data/account.json`（只有用户名、盐、哈希，没有明文密码）
- 会话密钥存 `data/session.key`（32 字节，权限尽量收紧）
- Cookie：HttpOnly + SameSite=Lax，默认 7 天
- 首次使用需 `setup`；忘记密码用 CLI：`python service.py passwd`
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime

from . import config

ACCOUNT_FILE = config.DATA_DIR / "account.json"
KEY_FILE = config.DATA_DIR / "session.key"
COOKIE = "nm_session"
TTL = 7 * 24 * 3600
ITERS = 200_000


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _unb64(text):
    return base64.b64decode((text or "").encode("ascii"))


def _key():
    try:
        k = KEY_FILE.read_bytes()
        if len(k) == 32:
            return k
    except Exception:
        pass
    k = secrets.token_bytes(32)
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(k)
    try:
        import os
        os.chmod(KEY_FILE, 0o600)
    except Exception:
        pass
    return k


def load():
    try:
        return json.loads(ACCOUNT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def initialized():
    d = load()
    return bool(d and d.get("username") and d.get("hash"))


def _hash(password, salt, iters=ITERS):
    return hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, iters)


def _write(data):
    ACCOUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        import os
        os.chmod(ACCOUNT_FILE, 0o600)
    except Exception:
        pass


def create(username, password):
    if initialized():
        return {"ok": False, "error": "管理员账号已存在"}
    u = (username or "").strip()
    if len(u) < 2:
        return {"ok": False, "error": "用户名至少 2 个字符"}
    if len(password or "") < 6:
        return {"ok": False, "error": "密码至少 6 位"}
    salt = secrets.token_bytes(16)
    _write({
        "username": u,
        "salt": _b64(salt),
        "hash": _b64(_hash(password, salt)),
        "iters": ITERS,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    return {"ok": True, "username": u}


def verify(username, password):
    d = load()
    if not d:
        return False
    if (username or "").strip() != d.get("username"):
        return False
    try:
        salt = _unb64(d.get("salt"))
        expect = _unb64(d.get("hash"))
        iters = int(d.get("iters") or ITERS)
    except Exception:
        return False
    got = _hash(password, salt, iters)
    return hmac.compare_digest(got, expect)


def change(old_password, new_username=None, new_password=None):
    """改用户名 / 改密码，必须验证旧密码。"""
    d = load()
    if not d:
        return {"ok": False, "error": "尚未创建管理员账号"}
    if not verify(d.get("username"), old_password):
        return {"ok": False, "error": "当前密码不正确"}

    u = (new_username or "").strip() or d["username"]
    if len(u) < 2:
        return {"ok": False, "error": "用户名至少 2 个字符"}
    if new_password:
        if len(new_password) < 6:
            return {"ok": False, "error": "新密码至少 6 位"}
        salt = secrets.token_bytes(16)
        d["salt"] = _b64(salt)
        d["hash"] = _b64(_hash(new_password, salt))
        d["iters"] = ITERS
    d["username"] = u
    d["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    _write(d)
    return {"ok": True, "username": u}


def reset_password(new_password):
    """CLI 兜底：忘记密码时重置（需要本机文件访问权限）。"""
    d = load()
    if not d:
        return {"ok": False, "error": "尚未创建管理员账号"}
    if len(new_password or "") < 6:
        return {"ok": False, "error": "密码至少 6 位"}
    salt = secrets.token_bytes(16)
    d["salt"] = _b64(salt)
    d["hash"] = _b64(_hash(new_password, salt))
    d["iters"] = ITERS
    d["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    _write(d)
    return {"ok": True, "username": d["username"]}


# ---------------- 会话令牌（username|exp|hmac） ----------------

def make_token(username, ttl=TTL):
    exp = int(time.time()) + int(ttl)
    body = "%s|%d" % (username, exp)
    sig = hmac.new(_key(), body.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return body + "|" + sig


def read_token(token, now=None):
    if not token:
        return None
    try:
        username, exp, sig = (token or "").rsplit("|", 2)
        exp = int(exp)
    except Exception:
        return None
    if exp < int(now or time.time()):
        return None
    expect = hmac.new(_key(), ("%s|%d" % (username, exp)).encode("utf-8"),
                      hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expect):
        return None
    d = load()
    if not d or d.get("username") != username:
        return None
    return username