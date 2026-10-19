"""OpenAI 兼容网关适配器测试（kunbot 文本入口）。

要点：
- 模型不在 /v1/models 清单时自动落到首项（网关会随时改挂，硬撞 404 伤全栈）；
- SSE chunk 形状归一化成 StreamDelta；data 坏行只丢当行；[DONE] 正常收尾；
- 建流时 429/5xx 走重试层分桶，4xx 撞一次即抛；
- usage 在 complete 路径透传给账本（不给就 None，不许捏造）。
"""

import json
from typing import Any

import pytest

import services.llm.openai_compat as compat
from core import config
from services.llm import retry as retry_mod


class _FakeResp:
    def __init__(self, status_code: int = 200, *, text: str = "", lines: list[str] | None = None,
                 payload: dict | None = None):
        self.status_code = status_code
        self.text = text
        self._lines = lines or []
        self._payload = payload or {}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError("fake raise")

    def json(self):
        return self._payload

    def iter_lines(self):
        return iter(self._lines)

    def iter_bytes(self):
        return iter([self.text.encode()])

    def close(self):
        self.closed = True


class _StreamCtx:
    def __init__(self, resp: _FakeResp):
        self._resp = resp

    def __enter__(self):
        return self._resp

    def __exit__(self, *a: Any):
        return False


class _FakeClient:
    """打桩 httpx.Client：按剧本回放 get/post/stream。"""

    def __init__(self, script: dict[str, list]):
        self._script = script
        self.calls: list[tuple[str, str, Any]] = []

    def get(self, path: str, **kw):
        self.calls.append(("GET", path, kw))
        return self._script.setdefault("GET", []).pop(0)

    def post(self, path: str, **kw):
        self.calls.append(("POST", path, kw))
        return self._script.setdefault("POST", []).pop(0)

    def stream(self, method: str, path: str, **kw):
        self.calls.append((method, path, kw))
        outcome = self._script.setdefault("STREAM", []).pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _StreamCtx(outcome)

    def close(self):
        pass


def _client(monkeypatch, script: dict[str, list]) -> compat.OpenAICompatChatClient:
    monkeypatch.setattr(compat.httpx, "Client", lambda **kw: _FakeClient(script))
    monkeypatch.setattr(compat, "_sleep", lambda _s: None)
    retry_mod._FAILURE_BUCKETS.clear()
    return compat.OpenAICompatChatClient("k-test", "http://gw.test/v1")


def _sse_lines(events: list[dict]) -> list[str]:
    return [f"data: {json.dumps(e)}" for e in events] + ["data: [DONE]"]


class TestModelResolution:
    def test_unknown_model_falls_back_to_first_hosted(self, monkeypatch):
        script = {"GET": [_FakeResp(payload={"data": [{"id": "deepseek-ai/DeepSeek-V4.1-Flash"}]})]}
        client = _client(monkeypatch, script)
        assert client._resolve_model("不存在-x") == "deepseek-ai/DeepSeek-V4.1-Flash"

    def test_known_model_passes_unchanged(self, monkeypatch):
        script = {"GET": [_FakeResp(payload={"data": [{"id": "m-a"}]})]}
        client = _client(monkeypatch, script)
        assert client._resolve_model("m-a") == "m-a"

    def test_discovery_failure_defers_to_requested(self, monkeypatch):
        script = {"GET": [_FakeResp(status_code=500, text="boom")]}
        client = _client(monkeypatch, script)
        assert client._resolve_model("m-y") == "m-y"  # 发现失败，直传原名交给上游判


class TestStreaming:
    def test_content_and_done_parse(self, monkeypatch):
        events = [
            {"choices": [{"delta": {"content": "你"}}]},
            {"choices": [{"delta": {"content": "好"}}]},
        ]
        script = {
            "GET": [_FakeResp(status_code=500)],
            "STREAM": [_FakeResp(lines=_sse_lines(events))],
        }
        client = _client(monkeypatch, script)
        stream = client.stream(model="m-y", messages=[{"role": "user", "content": "hi"}])
        pieces = [d.content for d in stream]
        assert pieces == ["你", "好"]

    def test_tool_call_delta_merged(self, monkeypatch):
        events = [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "search_web", "arguments": "{\"q"}},
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "\":\"hi\"}"}},
            ]}}]},
        ]
        script = {
            "GET": [_FakeResp(status_code=500)],
            "STREAM": [_FakeResp(lines=_sse_lines(events))],
        }
        client = _client(monkeypatch, script)
        deltas = [d for d in client.stream(model="m-y", messages=[])]
        assert len(deltas) == 2
        assert deltas[0].tool_calls[0].name == "search_web"
        assert deltas[1].tool_calls[0].arguments == '":"hi"}'

    def test_429_retries_once_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_MAX_RETRIES", 1)
        script = {
            "GET": [_FakeResp(status_code=500)],
            "STREAM": [_FakeResp(status_code=429, text="slow down"), _FakeResp(lines=_sse_lines([{"choices": [{"delta": {"content": "ok"}}]}]))],
        }
        client = _client(monkeypatch, script)
        deltas = [d.content for d in client.stream(model="m", messages=[])]
        assert deltas == ["ok"]
        assert retry_mod.get_failure_buckets() == {"rate_limit": 1}

    def test_400_fails_fast_no_retry(self, monkeypatch):
        script = {
            "GET": [_FakeResp(status_code=500)],
            "STREAM": [_FakeResp(status_code=400, text="bad model")],
        }
        client = _client(monkeypatch, script)
        with pytest.raises(retry_mod.HttpStatusFailure):
            next(iter(client.stream(model="m", messages=[])))
        assert retry_mod.get_failure_buckets() == {"client_error": 1}
        # 建流只试了一次，没重试（GET 是模型清单发现，不算重试）
        assert [c[0] for c in client._client.calls if c[0] == "POST"] == ["POST"]


class TestComplete:
    def test_content_and_usage_passthrough(self, monkeypatch):
        script = {
            "GET": [_FakeResp(status_code=500)],
            "POST": [_FakeResp(payload={
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            })],
        }
        client = _client(monkeypatch, script)
        reply = client.complete(model="m", messages=[])
        assert reply.content == "hello"
        assert reply.usage == {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}

    def test_usage_absent_is_none_not_zero(self, monkeypatch):
        script = {
            "GET": [_FakeResp(status_code=500)],
            "POST": [_FakeResp(payload={"choices": [{"message": {"content": "x"}}]})],
        }
        client = _client(monkeypatch, script)
        reply = client.complete(model="m", messages=[])
        assert reply.usage is None  # 宁缺不造数
