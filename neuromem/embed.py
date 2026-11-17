"""文本嵌入：Ollama / OpenAI 兼容，顺带记录用量。"""
import time

import requests

from . import config, settings, usage


class EmbedError(Exception):
    pass


def _cfg():
    return settings.current()["embed"]


def _dim_body(cfg, body):
    """若配置了向量维度就随请求发送 dimensions（Matryoshka 模型可指定输出维度）。
    不支持该参数的端点由调用方降级重试，避免老服务直接报错。"""
    dim = cfg.get("dim")
    if dim:
        body = dict(body)
        body["dimensions"] = int(dim)
    return body


def _ollama_embed(cfg, texts):
    base = settings.ollama_base(cfg.get("base_url"))
    model = cfg.get("model") or config.EMBED_MODEL
    timeout = cfg.get("timeout") or 30
    # keep_alive：默认策略下 ollama 冷启动要**重新加载 2.5GB 模型**，实测单次
    # 嵌入从 250ms 变成 50~70s（评测跑一轮 27 题被拖到半小时以上）。显式常驻
    # 一次都不再付这个代价；模型名或机器换掉时按配置覆盖即可。
    body = _dim_body(cfg, {"model": model, "input": texts,
                           "keep_alive": getattr(config, "EMBED_KEEP_ALIVE", "30m")})
    try:
        r = requests.post(f"{base}/api/embed", json=body, timeout=timeout)
        if not r.ok and r.status_code >= 400 and "dimensions" in body:
            # 老版 Ollama 不认 dimensions：去掉后重试一次
            body.pop("dimensions")
            r = requests.post(f"{base}/api/embed", json=body, timeout=timeout)
        if r.ok:
            d = r.json()
            embs = d.get("embeddings")
            if embs:
                return embs, d.get("prompt_eval_count") or 0
    except requests.RequestException:
        pass
    # 旧版接口只能逐条
    out = []
    for t in texts:
        r = requests.post(f"{base}/api/embeddings",
                          json={"model": model, "prompt": t}, timeout=timeout)
        r.raise_for_status()
        out.append(r.json()["embedding"])
    return out, 0


def _openai_embed(cfg, texts):
    url = settings.openai_base(cfg.get("base_url")) + "/embeddings"
    headers = settings.openai_headers(cfg)
    body = _dim_body(cfg, {"model": cfg.get("model"), "input": texts})
    r = requests.post(url, json=body, headers=headers,
                      timeout=cfg.get("timeout") or 30)
    if r.status_code >= 400 and "dimensions" in body:
        # 端点不支持 dimensions（多数老服务）：去掉后重试一次
        body.pop("dimensions")
        r = requests.post(url, json=body, headers=headers,
                          timeout=cfg.get("timeout") or 30)
    if r.status_code >= 400:
        detail = ""
        try:
            err = (r.json() or {}).get("error")
            detail = (err.get("message") if isinstance(err, dict) else str(err or "")) or ""
        except Exception:
            detail = (r.text or "")[:160]
        hint = ""
        if r.status_code in (401, 403):
            hint = " · API Key 无效，或该服务没有 embeddings 接口"
        elif r.status_code == 404:
            hint = " · 该端点不存在（很多对话服务不提供 embedding）"
        raise EmbedError("HTTP %d %s（embed/%s）%s"
                         % (r.status_code, detail[:160], cfg.get("model"), hint))
    d = r.json()
    items = d.get("data") or []
    if not items:
        raise EmbedError("嵌入返回空 data")
    items = sorted(items, key=lambda x: x.get("index", 0))
    toks = (d.get("usage") or {}).get("prompt_tokens") or 0
    return [it.get("embedding") for it in items], toks


def _embed_batch(texts):
    cfg = _cfg()
    provider = (cfg.get("provider") or "ollama").lower()
    model = cfg.get("model") or config.EMBED_MODEL
    t0 = time.time()
    try:
        if provider == "ollama":
            embs, toks = _ollama_embed(cfg, texts)
        elif provider in ("openai", "openai_compat"):
            embs, toks = _openai_embed(cfg, texts)
        else:
            raise EmbedError("未知 embed provider: %s" % provider)
    except Exception as e:
        usage.record("embed", "embed", provider, model, False,
                     (time.time() - t0) * 1000, error=str(e))
        raise
    usage.record("embed", "embed", provider, model, True,
                 (time.time() - t0) * 1000, toks, 0)
    return embs


def embed(text):
    """单条文本 -> 向量（list[float]）。"""
    embs = _embed_batch([text])
    if not embs:
        raise EmbedError("嵌入返回为空")
    return embs[0]


def embed_many(texts):
    """批量 -> 向量列表。"""
    if not texts:
        return []
    return _embed_batch(list(texts))


def stored_dim(conn):
    """库内已有向量的维度（element 为 float32），没有数据返回 None。"""
    row = conn.execute(
        "SELECT embedding FROM events WHERE embedding IS NOT NULL AND length(embedding)>0 LIMIT 1"
    ).fetchone()
    if not row or not row[0]:
        return None
    return len(row[0]) // 4