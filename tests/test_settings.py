"""配置层：默认值、保存/读回、掩码、端点工具。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neuromem import config, settings  # noqa: E402

orig = settings.current()
print("默认 provider:", orig["llm"]["provider"], "| base:", orig["llm"]["base_url"])
print("默认抽取/回答:", orig["llm"]["extract_model"], "/", orig["llm"]["answer_model"])
assert orig["llm"]["provider"] == "ollama"
assert orig["llm"]["extract_model"] == config.LLM_MODEL

assert settings.mask_key("") == ""
assert settings.mask_key("sk-1234567890abcd") == "****abcd"
assert settings.mask_key("short") == "****"

pub = settings.public(orig)
print("掩码后 key:", repr(pub["llm"]["api_key"]))
assert not pub["llm"]["api_key"].startswith("sk-")

assert settings.openai_base("https://api.deepseek.com") == "https://api.deepseek.com/v1"
assert settings.openai_base("https://api.deepseek.com/v1/") == "https://api.deepseek.com/v1"
assert settings.openai_base("http://127.0.0.1:11434") == "http://127.0.0.1:11434/v1"
assert settings.ollama_base("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"

try:
    saved = settings.save({"llm": {"answer_model": "test-model"}, "embed": {}})
    assert saved["llm"]["answer_model"] == "test-model"
    assert saved["llm"]["extract_model"] == orig["llm"]["extract_model"], "未提交的字段不应被清空"
    back = settings.load()
    assert back["llm"]["answer_model"] == "test-model", "持久化失败"
    print("保存/读回 OK:", back["llm"]["answer_model"])
finally:
    settings.save({"llm": orig["llm"], "embed": orig["embed"]})

restored = settings.current()
assert restored["llm"]["answer_model"] == orig["llm"]["answer_model"]
print("已恢复默认配置:", restored["llm"])
print("PASS")