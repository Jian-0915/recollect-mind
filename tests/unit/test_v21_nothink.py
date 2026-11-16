"""v21 回归：陪伴对话必须关掉思考模式。

背景（2026-11-16 实测）：端点上的模型全是思考型。同一条真实陪伴提示词，
sensenova-6.8-flash 要先花 44~76 个思考 token 才开口，首字延迟从 4.0s 涨到
5.2s；sensenova-6.8-flash-lite 更极端，800 的输出预算被思考烧光，正文截断成
非法 JSON，整轮降级成「我这边没组织好」。而关掉思考后回答**更短也更贴题**：

    「我开的什么车？」
      开思考 → 哎这个我翻了下，没找到你开啥车诶。不过记得你买了辆公路车…
      关思考 → 这个我还真不知道，你之前没跟我聊过车的事。开啥车呀？

所以这里守住三件事：参数真发出去了、端点不认时会退回而不是整轮报错、
关不掉时 trace 里看得见（不能只写在配置里假装关掉了）。
"""
import json

import pytest
import requests

from neuromem import config, llm


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)
        self.ok = status < 400

    def json(self):
        return self._payload


class FakeRequests:
    """只实现 llm.py 用到的那一点点：post + RequestException。"""
    RequestException = requests.RequestException

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None, **kw):
        self.calls.append(json)
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
def _clean_state():
    with llm._thinking_lock:
        llm._NO_THINK_OK.clear()
        llm._NO_THINK_BAD.clear()
    llm.LAST_THINK = {}
    yield


def _patch(monkeypatch, fake):
    monkeypatch.setattr(llm, "requests", fake)


def test_think_off_params_cover_major_vendors():
    p = llm._no_think_params()
    assert p["reasoning_effort"] == "none"
    assert p["thinking"] == {"type": "disabled"}
    assert p["enable_thinking"] is False
    assert p["chat_template_kwargs"] == {"enable_thinking": False}


def test_thinking_off_by_default():
    assert config.LLM_THINK_OFF is True


def test_first_request_already_has_think_off_params(monkeypatch):
    """关思考必须在**第一次**请求就带上，不能等被截断再补一次。"""
    fake = FakeRequests([FakeResp(200, ok_body(rtok=0))])
    _patch(monkeypatch, fake)
    txt, pt, ct = llm._call_openai(CFG, "hi", "m-1", True, 300, 30, 0.7, think=False)
    assert txt == '{"reply":"好"}'
    body = fake.calls[0]
    assert body["thinking"] == {"type": "disabled"}
    assert body["reasoning_effort"] == "none"
    assert body["response_format"] == {"type": "json_object"}
    assert llm.LAST_THINK["reasoning_tokens"] == 0
    assert llm.LAST_THINK["off_honored"] is True


def test_endpoint_rejecting_params_falls_back_and_remembers(monkeypatch):
    """严格端点（不认未知字段）400 时要退回原始请求，且下次不再白试。"""
    fake = FakeRequests([
        FakeResp(400, {"error": {"message": "Unrecognized request argument: thinking"}}),
        FakeResp(200, ok_body()),
        FakeResp(200, ok_body()),          # 第二次调用：不该再带参数
    ])
    _patch(monkeypatch, fake)
    txt, *_ = llm._call_openai(CFG, "hi", "m-2", True, 300, 30, 0.7, think=False)
    assert txt == '{"reply":"好"}'
    assert "thinking" in fake.calls[0]
    assert "thinking" not in fake.calls[1]      # 退回原始 body
    assert ("https://example.test/v1/chat/completions", "m-2") in llm._NO_THINK_BAD

    llm._call_openai(CFG, "hi", "m-2", True, 300, 30, 0.7, think=False)
    assert "thinking" not in fake.calls[2]      # 记住之后不再试


def test_still_thinking_is_visible_not_silent(monkeypatch):
    """端点忽略参数时不能假装关掉了：LAST_THINK 要如实报出思考 token。"""
    fake = FakeRequests([FakeResp(200, ok_body(rtok=151))])
    _patch(monkeypatch, fake)
    llm._call_openai(CFG, "hi", "m-3", True, 300, 30, 0.7, think=False)
    assert llm.LAST_THINK["reasoning_tokens"] == 151
    assert llm.LAST_THINK["off_requested"] is True
    assert llm.LAST_THINK["off_honored"] is False


def test_truncated_by_thinking_then_retried_without(monkeypatch):
    """思考把预算烧光（content 空 / 被截断）时，仍要能救回一次正文。"""
    fake = FakeRequests([
        FakeResp(200, ok_body(content="", rtok=200, finish="length")),
        FakeResp(200, ok_body(content='{"reply":"救回来"}', rtok=0)),
    ])
    _patch(monkeypatch, fake)
    txt, *_ = llm._call_openai(CFG, "hi", "m-4", True, 300, 30, 0.7, think=False)
    assert "救回来" in txt
    assert llm.LAST_THINK["reasoning_tokens"] == 0
    assert len(fake.calls) == 2


# ---------------- 陪伴 agent 侧 ----------------
def test_companion_passes_think_false(monkeypatch):
    """make_llm 的云端分支必须显式关思考，而不是靠端点默认值。"""
    seen = {}

    def fake_generate(prompt, **kw):
        seen.update(kw)
        return "{}"

    from agent import companion
    monkeypatch.setattr(companion.llm, "generate", fake_generate)
    fn = companion.make_llm()
    fn("提示词")
    assert seen.get("think") is False
    assert seen.get("json_mode") is True


def test_trace_records_think(monkeypatch, tmp_path):
    """trace 里要有 think 字段：关没关掉，看数据说话。"""
    from agent.companion import CompanionAgent

    def fake_llm(prompt):
        llm.LAST_THINK = {"reasoning_tokens": 0, "off_requested": True,
                          "off_honored": True, "params_sent": True,
                          "model": "fake"}
        return '{"reply":"你好呀","used_ids":[]}'

    with CompanionAgent(db_path=str(tmp_path / "t.db"), session_id="t",
                        llm_fn=fake_llm, trace_path=None) as ag:
        row = ag.step("你好")
    assert row["think"]["reasoning_tokens"] == 0
    assert row["think"]["off_honored"] is True
