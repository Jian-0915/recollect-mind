"""运行时模型配置：provider / base_url / api_key / 各角色模型名。

配置存 settings 表（key='models'），进程内缓存；保存后立即生效，无需重启。
未配置时回落到 config.py 的默认值（Ollama 本地）。
"""
import json
import threading
import time

from . import config, db, secret

_LOCK = threading.Lock()
_cache = None
_cache_ver = 0
# 跨进程失效：任何进程保存后 touch 版本文件，其他进程下次读取即重载
_VER_FILE = config.DATA_DIR / "settings.ver"


def _touch_version():
    try:
        _VER_FILE.parent.mkdir(parents=True, exist_ok=True)
        _VER_FILE.write_text(str(time.time_ns()), encoding="utf-8")
    except Exception:
        pass


def _version():
    try:
        return _VER_FILE.stat().st_mtime_ns
    except Exception:
        return 0

DEFAULTS = {
    "llm": {
        "provider": "ollama",           # ollama | openai_compat
        "base_url": config.OLLAMA_URL,
        "api_key": "",
        "extract_model": config.LLM_MODEL,
        "answer_model": config.ANSWER_MODEL,
        "timeout": config.LLM_TIMEOUT,
        "temperature": config.LLM_TEMPERATURE,
    },
    "embed": {
        "provider": "ollama",
        "base_url": config.OLLAMA_URL,
        "api_key": "",
        "model": config.EMBED_MODEL,
        "dim": config.EMBED_DIM,
        "timeout": config.EMBED_TIMEOUT,
    },
}

PRESETS = {
    # 本地全链路预设：模型名/维度一律取自 config，避免和已迁移的向量库不一致。
    # 历史坑：这里曾写死 bge-large:latest / 1024，用户一点「Ollama 预设」就把
    # 2560 维的库写回 1024 维，_bulk_cos 静默按短维计算且全程不报错。
    "ollama": {"provider": "ollama", "base_url": config.OLLAMA_URL,
               "extract_model": config.LLM_MODEL, "answer_model": config.ANSWER_MODEL,
               "model": config.EMBED_MODEL, "dim": config.EMBED_DIM,
               "timeout": config.EMBED_TIMEOUT},
    "deepseek": {"provider": "openai_compat", "base_url": "https://api.deepseek.com",
                 "extract_model": "deepseek-chat", "answer_model": "deepseek-chat"},
    "qwen": {"provider": "openai_compat",
             "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "extract_model": "qwen-plus", "answer_model": "qwen-max",
             "model": "text-embedding-v3", "dim": 1024},
    "moonshot": {"provider": "openai_compat", "base_url": "https://api.moonshot.cn/v1",
                 "extract_model": "moonshot-v1-8k", "answer_model": "moonshot-v1-8k"},
    "siliconflow": {"provider": "openai_compat", "base_url": "https://api.siliconflow.cn/v1",
                    "extract_model": "Qwen/Qwen2.5-7B-Instruct",
                    "answer_model": "Qwen/Qwen2.5-72B-Instruct",
                    "model": "BAAI/bge-large-zh-v1.5", "dim": 1024},
    "openai": {"provider": "openai_compat", "base_url": "https://api.openai.com/v1",
               "extract_model": "gpt-4o-mini", "answer_model": "gpt-4o-mini",
               "model": "text-embedding-3-small", "dim": 1536},
}


def _ensure_table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")


def load(conn=None):
    own = conn is None
    c = conn or db.connect()
    try:
        _ensure_table(c)
        row = c.execute("SELECT value FROM settings WHERE key='models'").fetchone()
        raw = json.loads(row["value"]) if row else {}
        c.commit()
    finally:
        if own:
            c.close()
    out = {k: dict(v) for k, v in DEFAULTS.items()}
    for section, vals in (raw or {}).items():
        if section in out and isinstance(vals, dict):
            out[section].update(vals)
    # 落库的 Key 可能是 DPAPI 密文：读出时解密（明文旧值原样保留）
    for sec in ("llm", "embed"):
        k = out[sec].get("api_key") or ""
        if secret.is_encrypted(k):
            out[sec]["api_key"] = secret.unprotect(k)
    return out


def current():
    """进程内缓存配置；其他进程保存过（版本文件变化）则自动重载。"""
    global _cache, _cache_ver
    v = _version()
    if _cache is not None and v == _cache_ver:
        return _cache
    with _LOCK:
        v2 = _version()
        if _cache is None or v2 != _cache_ver:
            _cache = load()
            _cache_ver = v2
    return _cache


def save(cfg, conn=None):
    global _cache, _cache_ver
    merged = {k: dict(v) for k, v in DEFAULTS.items()}
    current_cfg = load(conn)
    for section in ("llm", "embed"):
        merged[section].update(current_cfg.get(section, {}))
        add = dict((cfg or {}).get(section, {}) or {})
        if "api_key" in add:
            add["api_key"] = clean_key(add["api_key"])
        merged[section].update(add)
    own = conn is None
    c = conn or db.connect()
    try:
        _ensure_table(c)
        # 落库前加密 API Key（Windows DPAPI，绑定当前用户）
        persisted = {k: dict(v) for k, v in merged.items()}
        for sec in ("llm", "embed"):
            persisted[sec]["api_key"] = secret.protect(
                persisted[sec].get("api_key") or "")
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('models',?)",
                  (json.dumps(persisted, ensure_ascii=False),))
        c.commit()
    finally:
        if own:
            c.close()
    with _LOCK:
        _cache = merged
        if own:
            # 只有写真实库才广播版本；测试传临时 conn 不应惊动运行中的服务
            _touch_version()
        _cache_ver = _version()
    return merged


def reload():
    global _cache, _cache_ver
    with _LOCK:
        _cache = None
        _cache_ver = 0
    return current()


def clean_key(key):
    """去掉首尾空白、换行与零宽字符（复制 Key 时最常见的隐形坑）。"""
    if not key:
        return ""
    s = str(key)
    for ch in ("\u200b", "\u200c", "\u200d", "\ufeff", "\r", "\n", "\t", " "):
        s = s.replace(ch, "")
    return s.strip()


def key_info(key):
    k = clean_key(key)
    if not k:
        return {"set": False, "len": 0, "head": "", "tail": ""}
    return {"set": True, "len": len(k), "head": k[:3],
            "tail": "****" + k[-4:] if len(k) > 8 else "****"}


def mask_key(key):
    k = clean_key(key)
    if not k:
        return ""
    return "****" + k[-4:] if len(k) > 8 else "****"


def public(cfg=None):
    """对外展示：api_key 掩码 + 可核对的长度/尾号。"""
    cfg = cfg or current()
    out = json.loads(json.dumps(cfg))
    for name, section in out.items():
        if "api_key" in section:
            raw = (cfg.get(name) or {}).get("api_key", "")
            section["api_key"] = mask_key(raw)
            section["api_key_info"] = key_info(raw)
    return out


def ollama_base(url):
    """取 Ollama 原生端点；云端预设残留的 .../v1 必须忽略。

    曾出现 embed.provider=ollama 但 base_url 被写成 https://api.openai.com/v1：
    请求变成 /v1/api/embed → 走代理 502，嵌入静默降级为纯关键词检索，
    全库召回分数掉一半且概念/图式通道全灭，极难从现象反推原因。
    Ollama 原生端点不带 /v1，据此识别并回到本地默认。
    """
    u = (url or "").rstrip("/")
    if not u or u.endswith("/v1") or "/v1/" in u:
        u = config.OLLAMA_URL
    return u.rstrip("/")


def openai_base(url):
    """OpenAI 兼容端点：缺 /v1 时补齐。"""
    u = (url or "").rstrip("/")
    if not u:
        u = "http://127.0.0.1:11434/v1"
    if not u.endswith("/v1"):
        u += "/v1"
    return u


def openai_headers(cfg):
    headers = {"Content-Type": "application/json"}
    key = clean_key((cfg or {}).get("api_key") or "")
    if key:
        headers["Authorization"] = "Bearer " + key
    return headers