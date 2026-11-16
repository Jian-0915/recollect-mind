"""验证 openai_compat 分支：用 Ollama 的 /v1 兼容端点模拟云端，无需真实 Key。"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import embed, llm, settings  # noqa: E402

orig = settings.current()
print("起始:", orig["llm"]["provider"], "/", orig["embed"]["provider"])

try:
    settings.save({
        "llm": {"provider": "openai_compat", "base_url": "http://127.0.0.1:11434",
                "api_key": "ollama", "extract_model": "qwen3.5:4b",
                "answer_model": "qwen3:8b", "timeout": 120},
        "embed": {"provider": "openai_compat", "base_url": "http://127.0.0.1:11434",
                  "api_key": "ollama", "model": "bge-large:latest", "dim": 1024, "timeout": 60},
    })
    cur = settings.current()
    print("切换后:", cur["llm"]["provider"], "base=", cur["llm"]["base_url"],
          "->", settings.openai_base(cur["llm"]["base_url"]))

    t0 = time.time()
    txt = llm.generate("只回复两个字：好的", max_tokens=12, timeout=120)
    dt = time.time() - t0
    print(f"[cloud-llm] {dt:.1f}s -> {txt.strip()[:40]!r}")
    assert txt.strip(), "云端 LLM 返回空"

    t0 = time.time()
    vec = embed.embed("连接测试：小明在公园放风筝")
    dt = time.time() - t0
    print(f"[cloud-embed] {dt:.1f}s -> dim={len(vec)}")
    assert len(vec) == 1024, "维度与库内 1024 不一致"
    assert abs(sum(x * x for x in vec) - 1.0) < 0.1 or max(vec) > 0, "向量异常"

    js = llm.generate_json('只输出 JSON：{"ok":true}', max_tokens=30, timeout=120)
    print("[cloud-json]", js)
    assert isinstance(js, dict) and js.get("ok") is True
finally:
    settings.save({"llm": orig["llm"], "embed": orig["embed"]})
    back = settings.current()
    print("已恢复:", back["llm"]["provider"], "/", back["embed"]["provider"])

assert back["llm"]["provider"] == "ollama"
txt = llm.generate("只回复：OK", max_tokens=8, timeout=90)
print("[local-llm 回归]", repr(txt.strip()[:20]))
assert txt.strip()
vec = embed.embed("回归测试")
print("[local-embed 回归] dim=", len(vec))
assert len(vec) == 1024
print("PASS（云端路径 + 本地回归均正常）")