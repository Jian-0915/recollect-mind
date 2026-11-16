"""用「真实陪伴提示词」对比：开思考 / 关思考 / 换模型，谁的回答更贴题。

两条实验铁律（都踩过坑，不能再犯）：
1. **只在主库的副本上跑**：不开思考的对比要读真实候选记忆，但绝不能在主库写
   一轮会话/事实。
2. **必须在 import agent 之前锁定库路径**。``agent/__init__.py`` 会在导入时把
   ``NEUROMEM_DB`` 设成 ``data/agent_lab.db``（实验台默认库），而实验台的配置
   是 Ollama 默认值——先导入 neuromem 再 import agent 时路径已经定死，反过来
   就会拿着一张「sensenova」的模型名去问本地 Ollama，得到 404 model not found。
   所以这里先显式设好环境变量，再导入两个包。

用法：
    python tools/ab_thinking.py "我开的什么车？"
    python tools/ab_thinking.py "我上次去冰岛是什么时候？" "我最近在忙什么"
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MAIN_DB = ROOT / "data" / "neuromem.db"


def _make_copy():
    """把主库复制成临时库，并把运行时整体指过去。返回临时库路径。"""
    tmpdir = Path(tempfile.mkdtemp(prefix="abthink_"))
    dst = tmpdir / "ab.db"
    for suffix in ("", "-wal", "-shm"):
        s = Path(str(MAIN_DB) + suffix)
        if s.exists():
            shutil.copy2(s, str(dst) + suffix)
    os.environ["NEUROMEM_DB"] = str(dst)
    from neuromem import config, settings
    config.DB_PATH = dst
    settings.reload()
    return dst


DB = _make_copy()

import requests  # noqa: E402

from agent.companion import CompanionAgent  # noqa: E402
from neuromem import settings  # noqa: E402

VARIANTS = [
    ("sensenova-6.8-flash  默认(思考)", "sensenova-6.8-flash", {}),
    ("sensenova-6.8-flash  关思考(thinking:disabled)", "sensenova-6.8-flash",
     {"thinking": {"type": "disabled"}}),
    ("sensenova-6.8-flash  关思考(reasoning_effort:none)", "sensenova-6.8-flash",
     {"reasoning_effort": "none"}),
    ("sensenova-6.8-flash-lite 默认", "sensenova-6.8-flash-lite", {}),
    ("sensenova-6.8-flash-lite 关思考(thinking:disabled)",
     "sensenova-6.8-flash-lite", {"thinking": {"type": "disabled"}}),
]


def capture_prompt(question):
    """跑一轮但把提示词截下来，不让模型真回答（llm_fn 直接返回空 JSON）。"""
    held = {}

    def fake(prompt):
        held.setdefault("prompt", prompt)
        return '{"reply":"（占位）","used_ids":[]}'

    with CompanionAgent(db_path=str(DB), session_id="abthink",
                        llm_fn=fake, trace_path=None) as ag:
        ag.step(question)
    return held.get("prompt") or ""


def call(model, prompt, extra, max_tokens=800, timeout=180):
    cfg = settings.current()["llm"]
    url = settings.openai_base(cfg.get("base_url")) + "/chat/completions"
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "stream": False, "temperature": 0.7, "max_tokens": max_tokens,
            "response_format": {"type": "json_object"}}
    body.update(extra)
    t0 = time.time()
    try:
        r = requests.post(url, json=body, headers=settings.openai_headers(cfg),
                          timeout=timeout)
    except requests.RequestException as e:
        return {"err": "%s: %s" % (type(e).__name__, str(e)[:120]),
                "ms": int((time.time() - t0) * 1000), "url": url}
    ms = int((time.time() - t0) * 1000)
    if r.status_code >= 400:
        return {"err": "%d %s" % (r.status_code, (r.text or "")[:200]), "ms": ms,
                "url": url}
    d = r.json()
    ch = (d.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = msg.get("content") or ""
    u = d.get("usage") or {}
    det = u.get("completion_tokens_details") or {}
    try:
        reply = json.loads(content).get("reply") or ""
    except Exception:
        reply = "（JSON 不合规）" + content[:100]
    return {"ms": ms, "reason_tok": det.get("reasoning_tokens"),
            "ctok": u.get("completion_tokens"),
            "finish": ch.get("finish_reason"), "reply": reply[:200]}


def main():
    questions = sys.argv[1:] or ["我开的什么车？"]
    cfg = settings.current()["llm"]
    print("实验库：", DB)
    print("端点  ：", cfg.get("base_url"), "| 配置模型：", cfg.get("answer_model"))
    for q in questions:
        prompt = capture_prompt(q)
        print("\n" + "=" * 78)
        print("问题：", q, "｜提示词 %d 字" % len(prompt))
        for name, model, extra in VARIANTS:
            r = call(model, prompt, extra)
            if r.get("err"):
                print("  %-46s FAILED %s" % (name, r["err"][:160]))
                continue
            print("  %-46s %6dms reason_tok=%-4s ctok=%-4s %s"
                  % (name, r["ms"], r["reason_tok"], r["ctok"], r["finish"]))
            print("      → %s" % r["reply"].replace("\n", " ")[:180])


if __name__ == "__main__":
    main()
