"""扫描端点上的所有模型，标出哪个默认不「思考」。

背景：sensenova-6.8-flash 默认开思考——实测一次最小 JSON 请求就吃掉 151 个
思考 token、耗时 5s；关思考的三种参数（reasoning_effort / enable_thinking /
chat_template_kwargs）都无效：要么被忽略（reason_tok 照样 161），要么把思考
文本并进 content 直接顶到 length。陪伴对话要的是「听话、快、说人话」，
所以这里按实测挑模型，而不是猜。

用法：python tools/probe_thinking.py scan
"""
import json
import sys
import time

import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from neuromem import settings  # noqa: E402

PROMPT = '只输出 JSON，不要任何解释：{"reply":"你好呀","used_ids":[]}'


def probe(model, extra=None, max_tokens=200, timeout=180):
    cfg = settings.current()["llm"]
    url = settings.openai_base(cfg.get("base_url")) + "/chat/completions"
    body = {"model": model, "messages": [{"role": "user", "content": PROMPT}],
            "stream": False, "temperature": 0.2, "max_tokens": max_tokens}
    body.update(extra or {})
    t0 = time.time()
    r = requests.post(url, json=body, headers=settings.openai_headers(cfg),
                      timeout=timeout)
    ms = int((time.time() - t0) * 1000)
    if r.status_code >= 400:
        return {"model": model, "ok": False, "status": r.status_code,
                "err": (r.text or "")[:160], "ms": ms}
    d = r.json()
    ch = (d.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    u = d.get("usage") or {}
    det = u.get("completion_tokens_details") or {}
    return {"model": model, "ok": True, "ms": ms,
            "reason_tok": det.get("reasoning_tokens"),
            "reasoning_len": len(reasoning),
            "ctok": u.get("completion_tokens"),
            "finish": ch.get("finish_reason"),
            "json_ok": content.strip().startswith("{"),
            "content": content[:100]}


def scan():
    cfg = settings.current()["llm"]
    url = settings.openai_base(cfg.get("base_url")) + "/models"
    ids = [m.get("id") for m in
           ((requests.get(url, headers=settings.openai_headers(cfg),
                          timeout=30).json().get("data")) or [])]
    rows = []
    for mid in ids:
        if not mid:
            continue
        r = probe(mid)
        rows.append(r)
        if r.get("ok"):
            print("%-24s %6dms reason_tok=%-5s reasoning_len=%-5s ctok=%-4s "
                  "finish=%-8s json=%s" % (mid, r["ms"], r["reason_tok"],
                                           r["reasoning_len"], r["ctok"],
                                           r["finish"], r["json_ok"]))
        else:
            print("%-24s FAILED %s %s" % (mid, r.get("status"), r["err"][:80]))
    print("\n===== 不思考且直接给 JSON 的候选 =====")
    for r in rows:
        if r.get("ok") and not r.get("reason_tok") and r.get("json_ok"):
            print("  ", r["model"], "%dms" % r["ms"])
    print(json.dumps(rows, ensure_ascii=False)[:0])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "scan":
        scan()
    else:
        m = sys.argv[1] if len(sys.argv) > 1 else "sensenova-6.8-flash"
        print(json.dumps(probe(m), ensure_ascii=False, indent=2))
