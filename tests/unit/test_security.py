"""安全：API Key 加密存储、管理员账号与会话 Cookie。"""
import json
import sys
import types

from neuromem import account, auth, secret, settings


class _Req:
    def __init__(self, path="/api/overview", headers=None, params=None, cookies=None):
        self.url = types.SimpleNamespace(path=path)
        self.headers = headers or {}
        self.query_params = params or {}
        self.cookies = cookies or {}


def test_secret_roundtrip():
    enc = secret.protect("sk-abc123")
    if sys.platform == "win32":
        assert enc.startswith(secret.PREFIX) and enc != "sk-abc123"
    assert secret.unprotect(enc) == "sk-abc123"
    assert secret.unprotect("sk-plain") == "sk-plain", "旧明文要能继续读"
    assert secret.protect("") == ""


def test_api_key_encrypted_at_rest(memdb, conn):
    settings.save({"llm": {"provider": "openai_compat", "base_url": "https://x",
                           "api_key": "sk-test-123456", "extract_model": "m",
                           "answer_model": "m"}}, conn=conn)

    raw = json.loads(conn.execute("SELECT value FROM settings WHERE key='models'").fetchone()[0])
    stored = raw["llm"]["api_key"]
    if sys.platform == "win32":
        assert stored.startswith(secret.PREFIX), "落库的 Key 应为 DPAPI 密文"
        assert "sk-test-123456" not in stored
    assert settings.load(conn)["llm"]["api_key"] == "sk-test-123456", "读回要还原明文"


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(account, "ACCOUNT_FILE", tmp_path / "account.json")
    monkeypatch.setattr(account, "KEY_FILE", tmp_path / "session.key")


def test_setup_requires_credentials_length(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    assert account.initialized() is False
    assert account.create("a", "123456")["ok"] is False, "用户名太短"
    assert account.create("admin", "123")["ok"] is False, "密码太短"
    assert account.create("admin", "test123456")["ok"] is True
    assert account.create("other", "test123456")["ok"] is False, "只能有一个管理员"
    assert account.initialized() is True


def test_verify_and_change(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    account.create("admin", "test123456")
    assert account.verify("admin", "test123456") is True
    assert account.verify("admin", "wrong") is False
    assert account.verify("nobody", "test123456") is False

    assert account.change("wrong", new_username="boss")["ok"] is False, "旧密码必须校验"
    assert account.change("test123456", new_username="boss")["ok"] is True
    assert account.verify("boss", "test123456") is True
    assert account.change("test123456", new_password="newpass888")["ok"] is True
    assert account.verify("boss", "newpass888") is True
    assert account.verify("boss", "test123456") is False


def test_password_stored_hashed(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    account.create("admin", "test123456")
    raw = (tmp_path / "account.json").read_text(encoding="utf-8")
    assert "test123456" not in raw, "不能存明文密码"


def test_session_token_roundtrip_and_tamper(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    account.create("admin", "test123456")
    tok = account.make_token("admin")
    assert account.read_token(tok) == "admin"
    # 签名末两位本来就可能恰好是 "00"，固定拼 "00" 会有 1/256 概率没真的篡改
    tampered = tok[:-2] + ("ff" if tok[-2:] != "ff" else "00")
    assert account.read_token(tampered) is None, "篡改签名应失效"
    assert account.read_token(account.make_token("admin", ttl=-10)) is None, "过期应失效"
    assert account.read_token("garbage") is None


def test_auth_gate(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    # 未创建账号：业务接口一律拒绝（页面自己引导初始化）
    assert auth.allowed(_Req("/api/overview")) is False
    assert auth.allowed(_Req("/")) is True, "首页放行，页面负责弹登录框"
    assert auth.allowed(_Req("/api/health")) is True
    assert auth.allowed(_Req("/api/auth/me")) is True

    account.create("admin", "test123456")
    good = {"nm_session": account.make_token("admin")}
    assert auth.allowed(_Req("/api/overview")) is False
    assert auth.allowed(_Req(cookies=good)) is True
    assert auth.allowed(_Req("/api/overview", cookies={"nm_session": "bad"})) is False