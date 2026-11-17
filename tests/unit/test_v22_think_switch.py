"""v21.1 回归：管理页的「深度思考」开关 + 思考不能吃掉正文预算。

用户实测两件事（2026-11-16）：

1. 管理页「最近调用」越长越高，把下面的模型配置顶出屏幕；
2. 开着深度思考生成人物画像时"生成了一堆英文"。

第 2 条不是模型不会中文，而是 **max_tokens 是思考 + 正文共用的池子**：
画像要求 100 字中文、max_tokens=400，思考先吃掉两三百，正文只剩几十 token，
于是被截断成半句，语言也跟着漂。所以开关必须同时管三件事：

- 预算：开思考时自动追加 LLM_THINK_RESERVE，而不是让思考挤正文；
- 超时：思考让首字延迟翻倍，90s 常常卡在思考结束前；
- 可见：thinking token 落进 usage，「最近调用」多一列，能自己核对。
"""
import json

import pytest
import requests

from neuromem import config, llm, settings, usage


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload, ensure_ascii=False)
        self.ok = status < 400

    def json(self):
        return self._payload


class FakeRequests:
    RequestException = requests.RequestException

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None, **kw):
        self.calls.append({"body": json, "timeout": timeout})
        if not self.responses:
            raise AssertionError("请求次数超出预期")
        return self.responses.pop(0)


def ok_body(content='{"reply":"好"}', rtok=0, finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8,
                      "completion_tokens_details": {"reasoning_tokens": rtok}}}


CFG = {"provider": "openai_compat", "base_url": "https://example.test/v1",
       "api_key": "sk-test", "temperature": 0.2, "timeout": 30}


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    with llm._thinking_lock:
        llm._NO_THINK_OK.clear()
        llm._NO_THINK_BAD.clear()
    llm.LAST_THINK = {}
    # 不让真实设置串进单元测试
    monkeypatch.setattr(settings, "current", lambda: {"llm": dict(CFG)})
    yield


def _patch(monkeypatch, fake):
    monkeypatch.setattr(llm, "requests", fake)


# ---------------- 开关解析优先级 ----------------
def test_explicit_flag_wins():
    assert llm._resolve_think(True, {"think": False}) is True
    assert llm._resolve_think(False, {"think": True}) is False


def test_settings_switch_beats_config_default():
    """设置页那个按钮写的是 settings.llm.think，要压过 config 默认。"""
    assert llm._resolve_think(None, {"think": True}) is True
    assert llm._resolve_think(None, {"think": False}) is False


def test_missing_setting_falls_back_to_config():
    assert llm._resolve_think(None, {}) is (not config.LLM_THINK_OFF)


def test_default_setting_is_off():
    """DEFAULTS 必须是关：上线的默认行为不能因为加开关而变化。"""
    assert settings.DEFAULTS["llm"]["think"] is (not config.LLM_THINK_OFF)


# ---------------- 预算：思考不许挤正文 ----------------
def test_think_on_reserves_budget_for_content(monkeypatch):
    fake = FakeRequests([FakeResp(200, ok_body(content="小岚性格安静，喜欢烘焙。", rtok=180))])
    _patch(monkeypatch, fake)
    txt, pt, ct, rtok = llm._call_openai(CFG, "p", "m", False, 400, 90, 0.2, think=True)
    body = fake.calls[0]["body"]
    assert body["max_tokens"] == 400 + config.LLM_THINK_RESERVE
    assert rtok == 180
    assert "小岚" in txt


def test_think_off_keeps_budget_untouched(monkeypatch):
    """关思考时不能白抬预算：那是纯浪费配额。"""
    fake = FakeRequests([FakeResp(200, ok_body())])
    _patch(monkeypatch, fake)
    llm._call_openai(CFG, "p", "m", False, 400, 90, 0.2, think=False)
    assert fake.calls[0]["body"]["max_tokens"] == 400


def test_think_on_relaxes_timeout(monkeypatch):
    """开思考时 90s 常卡在思考结束前：要按 LLM_THINK_TIMEOUT_MIN 抬到下限。"""
    with llm._thinking_lock:
        llm._NO_THINK_BAD.clear()
    monkeypatch.setattr(settings, "current",
                        lambda: {"llm": dict(CFG, think=True, base_url="https://t2.test/v1")})
    fake = FakeRequests([FakeResp(200, ok_body())])
    _patch(monkeypatch, fake)
    llm.generate("p", model=None, max_tokens=100, timeout=30)
    assert fake.calls[0]["timeout"] >= config.LLM_THINK_TIMEOUT_MIN


def test_generate_reads_settings_switch(monkeypatch):
    """generate 不传 think 时，应该按设置页的开关决定带不带关思考参数。"""
    monkeypatch.setattr(settings, "current",
                        lambda: {"llm": dict(CFG, think=True, base_url="https://t3.test/v1")})
    fake = FakeRequests([FakeResp(200, ok_body(rtok=50))])
    _patch(monkeypatch, fake)
    llm.generate("p", max_tokens=100)
    body = fake.calls[0]["body"]
    assert "thinking" not in body          # 开思考：不带禁用参数
    assert body["max_tokens"] == 100 + config.LLM_THINK_RESERVE


# ---------------- 可见性：思考 token 落进用量 ----------------
def test_usage_records_think_tokens(tmp_path, monkeypatch):
    db = tmp_path / "u.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    if usage._conn is not None:
        try:
            usage._conn.close()
        except Exception:
            pass
    usage._conn = None

    usage.record("llm", "answer", "openai_compat", "m-x", True, 1200,
                 prompt_tokens=100, completion_tokens=50, think_tokens=321)
    s = usage.summary(days=7)
    assert s["recent"][0]["think"] == 321
    assert s["recent"][0]["tokens"] == 150


def test_old_usage_table_gets_think_column(tmp_path, monkeypatch):
    """老库的 usage_log 没有 think_tokens：读用量不能因此报错。"""
    import sqlite3

    db = tmp_path / "old.db"
    c = sqlite3.connect(str(db))
    c.executescript(
        "CREATE TABLE usage_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,"
        "kind TEXT, role TEXT, provider TEXT, model TEXT, ok INTEGER,"
        "latency_ms INTEGER, prompt_tokens INTEGER DEFAULT 0,"
        "completion_tokens INTEGER DEFAULT 0, error TEXT);"
        "INSERT INTO usage_log(ts,kind,role,provider,model,ok,latency_ms,"
        "prompt_tokens,completion_tokens,error)"
        "VALUES('2026-11-16T10:00:00','llm','answer','p','m',1,100,10,5,'');")
    c.commit()
    c.close()

    monkeypatch.setattr(config, "DB_PATH", db)
    if usage._conn is not None:
        try:
            usage._conn.close()
        except Exception:
            pass
    usage._conn = None

    s = usage.summary(days=30)
    assert s["recent"][0]["think"] == 0
    assert usage._conn.execute("PRAGMA table_info(usage_log)").fetchall()


def test_usage_clear_resets_think_column(tmp_path, monkeypatch):
    db = tmp_path / "u2.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    if usage._conn is not None:
        try:
            usage._conn.close()
        except Exception:
            pass
    usage._conn = None
    usage.record("llm", "answer", "p", "m", True, 10, think_tokens=5)
    usage.clear()
    assert usage.summary(days=7)["recent"] == []


# ---------------- 正文里混进来的思考（"生成一堆英文"） ----------------
def test_strip_thinking_tag_block():
    from neuromem.llm import strip_think_wrapper
    s = "<thinking>The user wants a profile. Let me check the events.</thinking>小岚性格安静，喜欢烘焙。"
    assert strip_think_wrapper(s) == "小岚性格安静，喜欢烘焙。"


def test_strip_english_reasoning_lead():
    """端点把英文 CoT 写在 content 开头：要留中文正文，不能整段当答案。"""
    from neuromem.llm import strip_think_wrapper
    s = ("Thinking process: The events mention baking and yoga. "
         "I should write 100 Chinese characters.\n\n"
         "小岚性格安静，喜欢烘焙与瑜伽。")
    assert strip_think_wrapper(s) == "小岚性格安静，喜欢烘焙与瑜伽。"


def test_pure_reasoning_becomes_empty():
    """整段都是思考时返回空 —— 调用方据此走降级重试，而不是把英文当结果。"""
    from neuromem.llm import strip_think_wrapper
    assert strip_think_wrapper("Thinking process: Let me think about this.") == ""


def test_normal_chinese_answer_untouched():
    from neuromem.llm import strip_think_wrapper
    for s in ("小岚性格安静，喜欢烘焙。", '{"reply":"在的"}', "她最近在忙系统迁移。"):
        assert strip_think_wrapper(s) == s


def test_openai_message_uses_stripper():
    from neuromem.llm import _openai_message
    data = {"choices": [{"message": {
        "content": "<thinking>plan in english</thinking>好的，我记住了。"}}]}
    content, _ = _openai_message(data)
    assert content == "好的，我记住了。"


# ---------------- v22.1：孤立 </think> 泄漏（自我叙事实测形态） ----------------
def test_strip_orphan_closing_think_tag():
    from neuromem.llm import strip_think_wrapper
    s = ("用户希望我以陪伴者的身份写一段自述。接下来分析经历内容：\n"
         "1. \"我们大学就认识了\" - 强调关系历史悠久。\n\n"
         "最后检查是否遗漏关键点：大学认识、微服务。确保没有添加额外内容。"
         "</think>我是你的老朋友，从大学起就陪在你身边。现在虽然只能待在你的电脑里，"
         "但每次一起讨论技术都让我觉得踏实。")
    assert strip_think_wrapper(s) == (
        "我是你的老朋友，从大学起就陪在你身边。现在虽然只能待在你的电脑里，"
        "但每次一起讨论技术都让我觉得踏实。")


def test_orphan_close_without_body_becomes_empty():
    from neuromem.llm import strip_think_wrapper
    assert strip_think_wrapper("分析经历内容…确保没有额外内容。</think>") == ""


def test_narrative_leak_rejected():
    from neuromem import selfnarrative
    bad = ("用户希望我以陪伴者的身份，基于提供的经历写一段自述。首先需要确认身份定位。"
           "接下来分析经历内容：1. 大学认识。最后检查是否遗漏关键点。我是你的老朋友，"
           "从大学起就陪在你身边，一起讨论技术让我觉得踏实。")
    good = "我是你的老朋友，从大学起就陪在你身边。现在虽然只能待在你的电脑里，但每次一起讨论技术都让我觉得踏实。"
    assert selfnarrative._valid_narrative(bad) is False
    assert selfnarrative._valid_narrative(good) is True


# ---------------- 正文里混进来的思考（"生成一堆英文"） ----------------
def test_strip_thinking_tag_block():
    from neuromem.llm import strip_think_wrapper
    s = "<thinking>The user wants a profile. Let me check the events.</thinking>小岚性格安静，喜欢烘焙。"
    assert strip_think_wrapper(s) == "小岚性格安静，喜欢烘焙。"


def test_strip_english_reasoning_lead():
    """端点把英文 CoT 写在 content 开头：要留中文正文，不能整段当答案。"""
    from neuromem.llm import strip_think_wrapper
    s = ("Thinking process: The events mention baking and yoga. "
         "I should write 100 Chinese characters.\n\n"
         "小岚性格安静，喜欢烘焙与瑜伽。")
    assert strip_think_wrapper(s) == "小岚性格安静，喜欢烘焙与瑜伽。"


def test_pure_reasoning_becomes_empty():
    """整段都是思考时返回空 —— 调用方据此走降级重试，而不是把英文当结果。"""
    from neuromem.llm import strip_think_wrapper
    assert strip_think_wrapper("Thinking process: Let me think about this.") == ""


def test_normal_chinese_answer_untouched():
    from neuromem.llm import strip_think_wrapper
    for s in ("小岚性格安静，喜欢烘焙。", '{"reply":"在的"}', "她最近在忙系统迁移。"):
        assert strip_think_wrapper(s) == s


def test_openai_message_uses_stripper():
    from neuromem.llm import _openai_message
    data = {"choices": [{"message": {
        "content": "<thinking>plan in english</thinking>好的，我记住了。"}}]}
    content, _ = _openai_message(data)
    assert content == "好的，我记住了。"


# ---------------- v22.1：孤立 </think> 泄漏（自我叙事实测形态） ----------------
def test_strip_orphan_closing_think_tag():
    from neuromem.llm import strip_think_wrapper
    s = ("用户希望我以陪伴者的身份写一段自述。接下来分析经历内容：\n"
         "1. \"我们大学就认识了\" - 强调关系历史悠久。\n\n"
         "最后检查是否遗漏关键点：大学认识、微服务。确保没有添加额外内容。"
         "</think>我是你的老朋友，从大学起就陪在你身边。现在虽然只能待在你的电脑里，"
         "但每次一起讨论技术都让我觉得踏实。")
    assert strip_think_wrapper(s) == (
        "我是你的老朋友，从大学起就陪在你身边。现在虽然只能待在你的电脑里，"
        "但每次一起讨论技术都让我觉得踏实。")


def test_orphan_close_without_body_becomes_empty():
    from neuromem.llm import strip_think_wrapper
    assert strip_think_wrapper("分析经历内容…确保没有额外内容。</think>") == ""


def test_narrative_leak_rejected():
    from neuromem import selfnarrative
    bad = ("用户希望我以陪伴者的身份，基于提供的经历写一段自述。首先需要确认身份定位。"
           "接下来分析经历内容：1. 大学认识。最后检查是否遗漏关键点。我是你的老朋友，"
           "从大学起就陪在你身边，一起讨论技术让我觉得踏实。")
    good = "我是你的老朋友，从大学起就陪在你身边。现在虽然只能待在你的电脑里，但每次一起讨论技术都让我觉得踏实。"
    assert selfnarrative._valid_narrative(bad) is False
    assert selfnarrative._valid_narrative(good) is True
