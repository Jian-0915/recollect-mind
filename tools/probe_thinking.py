"""探测云端端点是否在「思考」，以及各家关闭参数是否被接受。

用法：python tools/probe_thinking.py [model]

对每个候选参数各发一次**完全相同**的最小请求，比对：
  - 总耗时（思考型模型会把延迟拉大数倍）
  - completion_tokens（思考 token 也算进预算）
  - 响应里是否带 reasoning / reasoning_content 字段
  - content 是否为空（预算被思考吃掉时，content 会是空或被截断）

只看事实，不做判断；结论写回 README 时按实测数字说话。
"""
import json
import sys
import time

import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from neuromem import config, llm, settings  # noqa: E402

PROMPT = ('只输出 JSON，不要任何解释：{"reply":"你好呀","used_ids":[]}')

VARIANTS = [
    ("baseline", {}),
    ("reasoning_effort=none", {"reasoning_effort": "none"}),
    ("enable_thinking=false", {"enable_thinking": False}),
    ("chat_template_kwargs", {"chat_template_kwargs": {"enable_thinking": False}}),
    ("thinking=disabled", {"thinking": {"type": "disabled"}}),
]


def call(url, headers, model, extra, max_tokens=200, timeout=180):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": False,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    body.update(extra)
    t0 = time.time()
    try:
        r = requests.post(url, json=body, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        return {"ok": False, "err": "%s: %s" % (type(e).__name__, str(e)[:120]),
                "ms": int((time.time() - t0) * 1000)}
    ms = int((time.time() - t0) * 1000)
    if r.status_code >= 400:
        return {"ok": False, "status": r.status_code,
                "err": (r.text or "")[:200], "ms": ms}
    d = r.json()
    ch = (d.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    u = d.get("usage") or {}
    det = u.get("completion_tokens_details") or {}
    return {"ok": True, "ms": ms, "content": content[:120],
            "reasoning_len": len(reasoning),
            "reasoning_head": reasoning[:80],
            "ptok": u.get("prompt_tokens"), "ctok": u.get("completion_tokens"),
            "reason_tok": det.get("reasoning_tokens"),
            "finish": ch.get("finish_reason")}


def main():
    cfg = settings.current()["llm"]
    model = sys.argv[1] if len(sys.argv) > 1 else (cfg.get("answer_model")
                                                   or config.ANSWER_MODEL)
    url = settings.openai_base(cfg.get("base_url")) + "/chat/completions"
    headers = settings.openai_headers(cfg)
    print("endpoint:", url)
    print("model   :", model)
    out = []
    for name, extra in VARIANTS:
        r = call(url, headers, model, extra)
        r["variant"] = name
        out.append(r)
        print("\n[%s]" % name)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    print("\n===== 汇总 =====")
    for r in out:
        if r.get("ok"):
            print("%-22s %6dms  ctok=%-4s reason_tok=%-4s reasoning_len=%-4d finish=%s"
                  % (r["variant"], r["ms"], r.get("ctok"), r.get("reason_tok"),
                     r["reasoning_len"], r.get("finish")))
        else:
            print("%-22s FAILED %s %s" % (r["variant"], r.get("status") or "",
                                          str(r.get("err"))[:120]))


if __name__ == "__main__":
    main()
