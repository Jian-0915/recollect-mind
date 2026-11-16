"""LLM 调用封装：Ollama 本地 + OpenAI 兼容云端；顺带记录用量。"""
import json
import logging
import threading
import time

import requests

from . import config, settings, usage

log = logging.getLogger("neuromem.llm")

# 最近一次调用的思考开销。调用方（陪伴 agent）把它写进 trace，用来回答
# 「到底关掉没有」，而不是靠猜。
#   {"reasoning_tokens": 0, "off_requested": True, "off_honored": True,
#    "model": "sensenova-6.8-flash"}
LAST_THINK = {}

# 关思考参数是「一次带上、不认就退」：OpenAI 兼容网关通常忽略不认识的字段，
# 严格端点会 400。每个 (端点, 模型) 只试一次，结果记在下面两个集合里，
# 避免每次对话都白付一次失败请求。
_thinking_lock = threading.Lock()
_NO_THINK_OK = set()      # 确认接受关思考参数
_NO_THINK_BAD = set()     # 明确拒绝（400/422 不认字段），以后不再试
_REJECT_HINT = ("unknown", "unrecognized", "not supported", "unsupported",
                "invalid", "extra", "additional", "unexpected")


class LLMError(Exception):
    pass


def _resolve_model(model):
    """把内部角色常量映射成用户配置的模型名。"""
    cfg = settings.current()["llm"]
    if not model:
        return cfg.get("answer_model") or config.ANSWER_MODEL
    if model == config.LLM_MODEL:
        return cfg.get("extract_model") or config.LLM_MODEL
    if model == config.ANSWER_MODEL:
        return cfg.get("answer_model") or config.ANSWER_MODEL
    return model


def _role_of(model):
    if model == config.LLM_MODEL:
        return "extract"
    if model == config.ANSWER_MODEL or model is None:
        return "answer"
    return "custom"


def _no_think_params():
    """各家「关思考」参数的合并体（键互不冲突，一次全带上）。

    为什么不是一个一个试：每一次试探都是一次完整请求，陪伴对话等不起。
    合并成一次的好处是「认了就省下全部思考开销，不认就退回去」，代价最多
    一次 400——而 400 这个结果本身值得记下来（见 _NO_THINK_BAD）。
    """
    out = {}
    for d in (config.LLM_NO_THINK_PARAMS or ()):
        out.update(d)
    return out


def _looks_rejected(text):
    t = str(text or "").lower()
    return any(h in t for h in _REJECT_HINT)


def _remember_think(key, ok):
    with _thinking_lock:
        if ok:
            _NO_THINK_OK.add(key)
        else:
            _NO_THINK_BAD.add(key)


def _think_allowed(key):
    with _thinking_lock:
        return key not in _NO_THINK_BAD


def _call_ollama(cfg, prompt, model, json_mode, max_tokens, timeout, temperature, think):
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": config.LLM_THINK if think is None else think,
        "options": {
            "num_predict": max_tokens or config.LLM_MAX_TOKENS,
            "temperature": temperature,
            "num_ctx": config.LLM_NUM_CTX,
            "repeat_penalty": 1.25,
        },
    }
    if json_mode:
        body["format"] = "json"
    url = settings.ollama_base(cfg.get("base_url")) + "/api/generate"
    r = requests.post(url, json=body, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    return (d.get("response", ""),
            d.get("prompt_eval_count") or 0,
            d.get("eval_count") or 0)


def _error_detail(resp, provider, model):
    """把服务端错误体解析成一句人话（DeepSeek/OpenAI 都会给 error.message）。"""
    detail = ""
    try:
        j = resp.json()
        err = j.get("error")
        detail = (err.get("message") if isinstance(err, dict) else str(err or "")) \
            or j.get("message") or ""
    except Exception:
        detail = (resp.text or "")[:200]
    hint = ""
    if resp.status_code in (401, 403):
        hint = " · API Key 无效或未生效，请重新生成后保存"
    elif resp.status_code == 404:
        hint = " · 模型名可能不存在，请核对"
    elif resp.status_code == 429:
        hint = " · 触发限流，稍后再试"
    return "HTTP %d %s（%s/%s）%s" % (resp.status_code, detail[:200], provider, model, hint)


def _openai_message(data):
    choices = data.get("choices") or []
    if not choices:
        return "", ""
    msg = choices[0].get("message") or {}
    content = msg.get("content") or ""
    if isinstance(content, list):  # 少数端点返回分段数组
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    return content, reasoning


def _openai_usage(data):
    u = (data or {}).get("usage") or {}
    return u.get("prompt_tokens") or 0, u.get("completion_tokens") or 0


def _openai_finish(data):
    choices = (data or {}).get("choices") or []
    return (choices[0].get("finish_reason") or "") if choices else ""


def _reason_tokens(data):
    u = (data or {}).get("usage") or {}
    det = u.get("completion_tokens_details") or {}
    return int(det.get("reasoning_tokens") or 0)


def _call_openai(cfg, prompt, model, json_mode, max_tokens, timeout, temperature,
                 think=None):
    """OpenAI 兼容调用；``think=False`` 时先带「关思考」参数发一次。

    v21：思考模式默认关掉，且**在第一次请求就关**。旧版只在 content 为空 /
    被截断之后才补试参数，等于每轮都先付一遍思考开销：实测同一条陪伴提示词，
    sensenova-6.8-flash 要想 44~76 个思考 token，-lite 甚至把 800 的预算全花
    在思考上、正文截断成非法 JSON（整轮降级成「我这边没组织好」）。
    """
    url = settings.openai_base(cfg.get("base_url")) + "/chat/completions"
    headers = settings.openai_headers(cfg)
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens or config.LLM_MAX_TOKENS,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    want_off = config.LLM_THINK_OFF if think is None else (think is False)
    key = (url, model)
    params = _no_think_params() if (want_off and _think_allowed(key)) else {}
    sent = dict(body)
    if params:
        sent.update(params)

    r = requests.post(url, json=sent, headers=headers, timeout=timeout)
    if params and r.status_code in (400, 422) and _looks_rejected(r.text):
        # 端点不认这些字段：退回不带参数的原始请求，并记住这个端点
        _remember_think(key, False)
        log.warning("端点 %s 不接受关思考参数，本进程不再尝试：%s",
                    url, (r.text or "")[:120])
        params = {}          # 后面 response_format 的兜底重试不能再把它们带上
        r = requests.post(url, json=body, headers=headers, timeout=timeout)
    elif params and r.ok:
        _remember_think(key, True)

    if r.status_code == 400 and json_mode and "response_format" in (r.text or ""):
        # 部分兼容端点不支持 response_format，去掉后重试
        body.pop("response_format", None)
        sent.pop("response_format", None)
        r = requests.post(url, json=sent if params else body,
                          headers=headers, timeout=timeout)
    if r.status_code >= 400:
        raise LLMError(_error_detail(r, "openai_compat", model))
    data = r.json()
    content, reasoning = _openai_message(data)
    finish = _openai_finish(data)
    rtok = _reason_tokens(data)
    _note_think(model, want_off, rtok, bool(params))
    # 思考型模型常出现"预算被思考吃掉"：content 为空，或只剩几十字就被截断
    truncated = (finish == "length" and len(content.strip()) < 12)
    if content.strip() and not truncated:
        return content, *_openai_usage(data)

    # 仍被思考拖垮：这次**逐个别家参数**地试（合并体已证明不管用），并抬高预算
    if reasoning or not content.strip() or truncated:
        for extra in ({"reasoning_effort": "none"}, {"thinking": {"type": "disabled"}},
                      {"enable_thinking": False},
                      {"chat_template_kwargs": {"enable_thinking": False}}):
            body2 = dict(body)
            body2.update(extra)
            body2["max_tokens"] = max(int(body2.get("max_tokens") or 0), 512)
            try:
                r2 = requests.post(url, json=body2, headers=headers, timeout=timeout)
                if not r2.ok:
                    continue
                d2 = r2.json()
                c2, _ = _openai_message(d2)
                f2 = _openai_finish(d2)
                if c2.strip() and not (f2 == "length" and len(c2.strip()) < 12):
                    _note_think(model, True, _reason_tokens(d2), True)
                    return c2, *_openai_usage(d2)
            except requests.RequestException:
                continue
    return content, *_openai_usage(data)


def _note_think(model, off_requested, rtok, params_sent):
    """记下这次真实的思考开销，并在「要求关却仍在思考」时留一条告警。"""
    global LAST_THINK
    honored = rtok == 0
    LAST_THINK = {"reasoning_tokens": rtok, "off_requested": bool(off_requested),
                  "off_honored": bool(honored), "params_sent": bool(params_sent),
                  "model": model}
    if off_requested and not honored:
        log.warning("模型 %s 仍在思考（%d tokens）：关思考参数被忽略，"
                    "回答会变慢且可能与问题无关", model, rtok)



def generate(prompt, *, model=None, json_mode=False, max_tokens=None, timeout=None,
             temperature=None, think=None):
    cfg = settings.current()["llm"]
    provider = (cfg.get("provider") or "ollama").lower()
    real_model = _resolve_model(model)
    role = _role_of(model)
    temp = cfg.get("temperature") if temperature is None else temperature
    if temp is None:
        temp = config.LLM_TEMPERATURE
    t = timeout or cfg.get("timeout") or config.LLM_TIMEOUT
    t0 = time.time()

    try:
        if provider == "ollama":
            txt, pt, ct = _call_ollama(cfg, prompt, real_model, json_mode,
                                       max_tokens, t, temp, think)
            _note_think(real_model, not (config.LLM_THINK if think is None else think),
                        0, False)
        elif provider in ("openai", "openai_compat"):
            txt, pt, ct = _call_openai(cfg, prompt, real_model, json_mode,
                                       max_tokens, t, temp, think)
        else:
            raise LLMError("未知 provider: %s" % provider)
    except requests.Timeout as e:
        usage.record("llm", role, provider, real_model, False,
                     (time.time() - t0) * 1000, error="timeout")
        raise LLMError("LLM 超时（%ss，%s/%s）" % (t, provider, real_model)) from e
    except requests.RequestException as e:
        usage.record("llm", role, provider, real_model, False,
                     (time.time() - t0) * 1000, error=str(e))
        raise LLMError("LLM 请求失败（%s/%s）: %s" % (provider, real_model, e)) from e
    except Exception as e:
        usage.record("llm", role, provider, real_model, False,
                     (time.time() - t0) * 1000, error=str(e))
        raise

    usage.record("llm", role, provider, real_model, True,
                 (time.time() - t0) * 1000, pt, ct)
    return txt


def generate_json(prompt, *, model=None, max_tokens=256, timeout=45, retries=1):
    """强制 JSON 输出；解析失败自动重试。"""
    last = ""
    for _ in range(retries + 1):
        txt = generate(prompt, model=model, json_mode=True, max_tokens=max_tokens, timeout=timeout)
        last = txt
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            continue
    raise LLMError(f"JSON 解析失败: {last[:200]}")