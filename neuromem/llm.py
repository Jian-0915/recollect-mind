"""LLM 调用封装：Ollama 本地 + OpenAI 兼容云端；顺带记录用量。"""
import json
import logging
import re
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
_NO_THINK_OK = {}       # (url, model) -> 实际被端点接受的关思考参数（子集）
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

    先合并带上是为了省请求：端点全认就一次到位。但**合并被拒不代表每个
    参数都被拒**（实测 sensenova 只认 ``thinking``，却把 ``reasoning_effort``
    报成非法字段）——这时逐个探测、把认的那个缓存下来（见
    ``_call_openai`` 的 400 分支），代价只在首次多几条请求。
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
            _NO_THINK_OK.setdefault(key, {})
        else:
            _NO_THINK_BAD.add(key)


def _remember_ok_params(key, params):
    """记住这个 (端点, 模型) 实际接受的关思考参数，之后直接用它。"""
    with _thinking_lock:
        _NO_THINK_OK[key] = params


def _cached_ok_params(key):
    with _thinking_lock:
        return _NO_THINK_OK.get(key)


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


_THINK_BLOCK = re.compile(
    r"<(think|thinking|reasoning|analysis|thought)>(.*?)</\1>", re.S | re.I)
_THINK_LEAD = re.compile(
    r"^\s*(thinking process|reasoning process|reasoning|思考过程|推理过程)\s*[:：]\s*",
    re.I)
# 实测 v22.1：sensenova 在短 prompt（自我叙事/画像）上会输出**只有闭合标签**的
# 思考块——正文在前、</think> 在后，没有 <think> 开头。成对正则匹配不到，
# 整段英文思考就留在正文里（自我叙事 581 字全是思考+正文混排）。
# 这种形态一律取 </think> 之后的文字；没有闭合标签的正常回答不受影响。
_ORPHAN_THINK_CLOSE = re.compile(
    r"</(think|thinking|reasoning|analysis|thought)>\s*", re.I)


def strip_think_wrapper(text):
    """剥掉混在正文里的思考块。

    大部分端点把 CoT 放在 ``reasoning_content``，正文是干净的；但少数端点
    （自建 vLLM、某些中转）直接把推理写进 ``content`` 开头，格式是
    ``<thinking>…</thinking>`` 或 ``Thinking process: …``。这段通常是**英文**，
    于是用户看到的就是「生成了一堆英文」，而真正的中文正文在后面。
    剥完若为空，调用方会走既有的降级重试，不会把半截英文当结果。
    """
    out = _THINK_BLOCK.sub("", str(text or "")).strip()
    # 孤立闭合标签（无 <think> 开头）：取标签之后的正文。
    m_close = _ORPHAN_THINK_CLOSE.search(out)
    if m_close:
        out = out[m_close.end():].strip()
    m = _THINK_LEAD.match(out)
    if m:
        rest = out[m.end():]
        # 引导行之后：有空行就取空行后的正文；没有空行视为整段都是推理
        parts = re.split(r"\n\s*\n", rest, maxsplit=1)
        out = parts[1].strip() if len(parts) == 2 else ""
    return out


def _openai_message(data):
    choices = data.get("choices") or []
    if not choices:
        return "", ""
    msg = choices[0].get("message") or {}
    content = msg.get("content") or ""
    if isinstance(content, list):  # 少数端点返回分段数组
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    return strip_think_wrapper(content), reasoning


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

    v21.1：开思考时把 ``max_tokens`` 抬高 ``LLM_THINK_RESERVE``。思考与正文
    共用一个池子，不抬就等于让思考吃输出——画像生成（要求 100 字中文、
    max_tokens=400）开思考后正文会被截成几十字英文碎片。
    """
    url = settings.openai_base(cfg.get("base_url")) + "/chat/completions"
    headers = settings.openai_headers(cfg)
    want_off = config.LLM_THINK_OFF if think is None else (think is False)
    budget = int(max_tokens or config.LLM_MAX_TOKENS)
    if not want_off:
        budget += config.LLM_THINK_RESERVE
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": temperature,
        "max_tokens": budget,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    key = (url, model)
    params = (_cached_ok_params(key) or _no_think_params()) \
        if (want_off and _think_allowed(key)) else {}
    sent = dict(body)
    if params:
        sent.update(params)

    r = requests.post(url, json=sent, headers=headers, timeout=timeout)
    if params and r.status_code in (400, 422) and _looks_rejected(r.text):
        # 合并参数被拒 ≠ 全部参数被拒：实测 sensenova 报「ReasoningEffort
        # invalid, should be one of: low, high, max」只是 reasoning_effort
        # 这个字段非法，thinking:{"type":"disabled"} 是认的。整组放弃
        # 等于把思考重新放出来（glm-5.3 因此想了 1202 token）。
        # → 逐个参数试一次，找出端点认的那一个并缓存，只多花首几次请求。
        for d in (config.LLM_NO_THINK_PARAMS or ()):
            if not d:
                continue
            probe = dict(body)
            probe.update(d)
            try:
                r2 = requests.post(url, json=probe, headers=headers, timeout=timeout)
            except requests.RequestException:
                continue
            # 必须真 200 **且思考确实归零**才算「认」：glm-5.3 会接受
            # enable_thinking 字段但照样想 157 token；限流 400（tpm/rpm）
            # 不含拒绝关键词，同样不能当字段被接受。
            if not r2.ok:
                continue
            try:
                if _reason_tokens(r2.json()) > 0:
                    continue      # 字段收下了但没生效，试下一个
            except Exception:
                pass
            params, sent, r = d, probe, r2
            _remember_ok_params(key, d)
            log.info("端点 %s 只认关思考参数 %s", url, list(d))
            break
        else:
            _remember_think(key, False)
            log.warning("端点 %s 不接受任何关思考参数，本进程不再尝试：%s",
                        url, (r.text or "")[:120])
            params, sent = {}, dict(body)   # 后面 response_format 兼容重试不带它们
            r = requests.post(url, json=body, headers=headers, timeout=timeout)
    elif params and r.ok:
        _remember_ok_params(key, params)

    if r.status_code == 400 and json_mode and "response_format" in (r.text or ""):
        # 部分兼容端点不支持 response_format，去掉后重试
        body.pop("response_format", None)
        sent.pop("response_format", None)
        r = requests.post(url, json=sent, headers=headers, timeout=timeout)
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
        return content, *_openai_usage(data), rtok

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
                    return c2, *_openai_usage(d2), _reason_tokens(d2)
            except requests.RequestException:
                continue
    return content, *_openai_usage(data), rtok


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
    want_think = _resolve_think(think, cfg)
    if want_think:
        # 思考会让首字延迟翻倍：显式传的 90s 常常刚好卡在思考结束前，放宽下限
        t = max(t, config.LLM_THINK_TIMEOUT_MIN)

    try:
        if provider == "ollama":
            txt, pt, ct = _call_ollama(cfg, prompt, real_model, json_mode,
                                       max_tokens, t, temp, want_think)
            _note_think(real_model, not want_think, 0, False)
            rtok = 0
        elif provider in ("openai", "openai_compat"):
            txt, pt, ct, rtok = _call_openai(cfg, prompt, real_model, json_mode,
                                             max_tokens, t, temp, want_think)
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
                 (time.time() - t0) * 1000, pt, ct, think_tokens=rtok)
    return txt


def _resolve_think(think, cfg=None):
    """思考开关的优先级：**显式参数 > 设置页开关 > 环境/config 默认**。

    设置页那个按钮写进 settings.llm.think；调用方（如画像生成）仍可显式
    传 ``think=True/False`` 覆盖单次调用。
    """
    if think is not None:
        return bool(think)
    cfg = cfg if cfg is not None else (settings.current().get("llm") or {})
    v = cfg.get("think")
    if v is not None:
        return bool(v)
    return bool(config.LLM_THINK) and not config.LLM_THINK_OFF


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