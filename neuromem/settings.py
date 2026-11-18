"""运行时配置：模型（key='models'）+ 外部 MCP 服务（key='mcp_servers'）。

配置存 settings 表，进程内缓存；保存后立即生效，无需重启。
密钥一律 DPAPI 加密落库，掩码对外展示；未配置时回落到 config.py 默认值。
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
        # 深度思考开关（设置页可见）。默认关：陪伴对话要快、要贴题，
        # 开思考反而把预算烧在推理上、正文被截断（见 config.LLM_THINK_RESERVE）。
        "think": not config.LLM_THINK_OFF,
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
    # 商汤日日新（SenseNova Token Plan）：作者自用组合。
    # 抽取用 flash-lite（快、便宜、积分消耗低），对话用 flash（生成更稳）。
    "sensenova": {"provider": "openai_compat", "base_url": "https://token.sensenova.cn/v1",
                  "extract_model": "sensenova-6.8-flash-lite",
                  "answer_model": "sensenova-6.8-flash"},
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


# --------------------------------------------------------------------------
# 外部 MCP 服务（通用管理器）
# --------------------------------------------------------------------------
# 一条服务 = {id, name, url, api_key, tool, enabled}。协议统一走 MCP
# Streamable HTTP / JSON-RPC 2.0；tool 是搜索工具名（默认 search）。
_MCP_KEY = "mcp_servers"
_MCP_DEFAULT = [{
    "id": "anysearch",
    "name": "AnySearch",
    "url": "https://api.anysearch.com/mcp",
    "api_key": "",
    "tool": "search",
    "enabled": True,
}]
_MCP_FIELDS = {"id", "name", "url", "api_key", "tool", "enabled"}


def _mcp_normalize(items):
    """规范化条目列表：补默认值、过滤未知字段、保证 id 唯一。"""
    out, seen = [], set()
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        row = {k: v for k, v in raw.items() if k in _MCP_FIELDS}
        url = str(row.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        rid = str(row.get("id") or "").strip() or ("srv_" + url[8:28].replace("/", "_"))
        if rid in seen:
            continue
        seen.add(rid)
        out.append({
            "id": rid,
            "name": str(row.get("name") or rid).strip()[:40],
            "url": url,
            "api_key": str(row.get("api_key") or "").strip(),
            "tool": str(row.get("tool") or "search").strip() or "search",
            "enabled": bool(row.get("enabled", True)),
        })
    return out


def mcp_load(conn=None):
    """读服务列表，解密密钥。无配置时返回内置 AnySearch 默认条目。"""
    own = conn is None
    c = conn or db.connect()
    try:
        _ensure_table(c)
        row = c.execute("SELECT value FROM settings WHERE key=?", (_MCP_KEY,)).fetchone()
        raw = json.loads(row["value"]) if row else None
        c.commit()
    finally:
        if own:
            c.close()
    if raw is None:
        return [dict(x) for x in _MCP_DEFAULT]
    out = _mcp_normalize(raw)
    for item in out:
        k = item.get("api_key") or ""
        if secret.is_encrypted(k):
            item["api_key"] = secret.unprotect(k)
    return out


def mcp_save(items, conn=None):
    """保存服务列表，密钥逐条 DPAPI 加密，广播版本失效。"""
    global _cache, _cache_ver
    norm = _mcp_normalize(items)
    own = conn is None
    c = conn or db.connect()
    try:
        _ensure_table(c)
        persisted = []
        for item in norm:
            row = dict(item)
            row["api_key"] = secret.protect(row.get("api_key") or "")
            persisted.append(row)
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                  (_MCP_KEY, json.dumps(persisted, ensure_ascii=False)))
        c.commit()
    finally:
        if own:
            c.close()
    if own:
        _touch_version()
    return norm


def mcp_public(items=None):
    """对外展示：密钥掩码 + 可核对信息，绝不回显明文。"""
    out = []
    for item in (mcp_load() if items is None else items):
        row = dict(item)
        raw = row.get("api_key") or ""
        row["api_key"] = mask_key(raw)
        row["api_key_info"] = key_info(raw)
        out.append(row)
    return out


def mcp_active(items=None):
    """当前启用的服务：优先第一个 enabled，找不到就退回第一个。"""
    lst = mcp_load() if items is None else items
    for item in lst:
        if item.get("enabled"):
            return item
    return lst[0] if lst else None


# --------------------------------------------------------------------------
# 语音播放偏好（音色/语速/音量）
# --------------------------------------------------------------------------
# 设备级偏好，不进人设版本系统。必须存后端 settings 表而不是浏览器
# localStorage：桌面端每次启动端口顺延（_find_free_port），localStorage 按
# origin（含端口）隔离，端口一变偏好就"丢失"——用户实测每次重启都要重设。
_TTS_KEY = "tts_prefs"
_TTS_DEFAULT = {"voice": "vivian", "rate": 1.0, "volume": 1.0}
_TTS_VOICES = {"vivian", "serena", "uncle_fu", "dylan", "eric"}


def _tts_normalize(raw):
    """规范化并夹取范围，非法值回落默认，防止脏数据让播放出错。"""
    out = dict(_TTS_DEFAULT)
    if not isinstance(raw, dict):
        return out
    voice = raw.get("voice")
    if isinstance(voice, str) and voice in _TTS_VOICES:
        out["voice"] = voice
    for key, lo, hi in (("rate", 0.7, 1.4), ("volume", 0.0, 1.0)):
        val = raw.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out[key] = min(hi, max(lo, float(val)))
    return out


def tts_prefs_load(conn=None):
    """读语音偏好；无配置返回默认值。"""
    own = conn is None
    c = conn or db.connect()
    try:
        _ensure_table(c)
        row = c.execute("SELECT value FROM settings WHERE key=?", (_TTS_KEY,)).fetchone()
        raw = json.loads(row["value"]) if row else None
        c.commit()
    finally:
        if own:
            c.close()
    return _tts_normalize(raw)


def tts_prefs_save(prefs, conn=None):
    """保存语音偏好（规范化后落库），广播版本失效。"""
    norm = _tts_normalize(prefs)
    own = conn is None
    c = conn or db.connect()
    try:
        _ensure_table(c)
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                  (_TTS_KEY, json.dumps(norm, ensure_ascii=False)))
        c.commit()
    finally:
        if own:
            c.close()
    if own:
        _touch_version()
    return norm


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