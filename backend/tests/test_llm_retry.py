"""C26：LLM 适配器重试层——关掉 SDK 盲重试，改由带结构化日志/失败分桶的自建重试。

验收锚点：
1. SDK 构造必须 max_retries=0（否则两套重试叠加，预算失控）；
2. 瞬态类别（timeout/connection/rate_limit/server_error）在预算内可重试；
3. client_error（4xx≠429）重试不会自愈还烧钱——一次失败即抛，且必须落桶；
4. 重试耗尽后如实抛错（不让建流卡死悬停，编排器有 SSE error 体面收尾）；
5. 流式尝试墙钟走 LLM_STREAM_TIMEOUT_SECONDS（默认 60），非流式走请求墙钟。
"""

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from zhipuai.core._errors import APIConnectionError, APIStatusError, APITimeoutError

import services.llm.zhipu as zhipu
from core import config

_REQ = httpx.Request("POST", "http://llm.test/v4/chat/completions")


def _timeout_error():
    return APITimeoutError(request=_REQ)


def _status_error(code: int):
    return APIStatusError(f"http {code}", response=httpx.Response(code, request=_REQ))


class _FakeCompletions:
    """按剧本回放 create() 的结果：异常项抛异常，其余原样返回。"""

    def __init__(self, script: list[Any], calls: list[dict]):
        self._script = list(script)
        self._calls = calls

    def create(self, **kwargs):
        self._calls.append(kwargs)
        outcome = self._script.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _make_client(monkeypatch, script: list[Any]) -> tuple[Any, list[dict], list[dict]]:
    """构造打桩了 SDK 的 ZhipuChatClient，返回 (client, sdk_init_kwargs, create_calls)。"""
    init_kwargs: list[dict] = []
    create_calls: list[dict] = []
    completions = _FakeCompletions(script, create_calls)

    class FakeSDK:
        def __init__(self, **kwargs):
            init_kwargs.append(kwargs)
            self.chat = SimpleNamespace(completions=completions)

    monkeypatch.setattr("zhipuai.ZhipuAI", FakeSDK)
    monkeypatch.setattr(zhipu, "_sleep", lambda _s: None)  # 退避到零，测试不真等
    zhipu._FAILURE_BUCKETS.clear()
    return zhipu.ZhipuChatClient(api_key="test-key"), init_kwargs, create_calls


def _complete_reply():
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])


class TestSdkRetryDisabled:
    def test_sdk_client_is_constructed_with_max_retries_zero(self, monkeypatch):
        _, init_kwargs, _ = _make_client(monkeypatch, [_timeout_error()])
        assert init_kwargs[0]["max_retries"] == 0


class TestRetryOnTransientFailures:
    @pytest.mark.parametrize(
        "failure,category",
        [
            pytest.param(_timeout_error, "timeout", id="timeout"),
            pytest.param(lambda: _status_error(429), "rate_limit", id="rate_limit_429"),
            pytest.param(lambda: _status_error(503), "server_error", id="server_5xx"),
        ],
    )
    def test_retryable_category_recovers_on_second_attempt(self, monkeypatch, failure, category):
        client, _, calls = _make_client(monkeypatch, [failure(), _complete_reply()])
        reply = client.complete(model="m", messages=[])
        assert reply.content == "ok"
        assert len(calls) == 2  # 首次失败 + 1 次补齐（LLM_MAX_RETRIES=1）
        assert zhipu.get_failure_buckets() == {category: 1}

    def test_connection_error_is_retryable(self, monkeypatch):
        client, _, calls = _make_client(monkeypatch, [APIConnectionError(request=_REQ), _complete_reply()])
        reply = client.complete(model="m", messages=[])
        assert reply.content == "ok"
        assert len(calls) == 2
        assert zhipu.get_failure_buckets() == {"connection_error": 1}


class TestNonRetryableFailuresFastFail:
    def test_client_error_fails_fast_without_retry(self, monkeypatch):
        # 400/401 这类错重试不会自愈还烧钱：只允许闪现一次
        client, _, calls = _make_client(monkeypatch, [_status_error(400), "SHOULD_NOT_BE_REACHED"])
        with pytest.raises(APIStatusError):
            client.complete(model="m", messages=[])
        assert len(calls) == 1
        assert zhipu.get_failure_buckets() == {"client_error": 1}

    def test_unknown_exception_fails_fast_and_is_bucketed(self, monkeypatch):
        client, _, calls = _make_client(monkeypatch, [ValueError("boom")])
        with pytest.raises(ValueError):
            client.complete(model="m", messages=[])
        assert len(calls) == 1
        assert zhipu.get_failure_buckets() == {"unknown": 1}


class TestRetryBudgetExhaustion:
    def test_exhausted_budget_propagates_last_error(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_MAX_RETRIES", 1)
        client, _, calls = _make_client(monkeypatch, [_timeout_error(), _timeout_error(), "unreachable"])
        with pytest.raises(APITimeoutError):
            client.complete(model="m", messages=[])
        assert len(calls) == 2  # 1 次首发 + 1 次重试，预算耗尽即抛
        assert zhipu.get_failure_buckets() == {"timeout": 2}

    def test_zero_max_retries_means_no_retry_at_all(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_MAX_RETRIES", 0)
        client, _, calls = _make_client(monkeypatch, [_timeout_error()])
        with pytest.raises(APITimeoutError):
            client.complete(model="m", messages=[])
        assert len(calls) == 1


class TestAttemptTimeouts:
    def test_stream_attempt_uses_stream_wall_clock_budget(self, monkeypatch):
        # create 返回空列表即「零条 chunk 的裸流」；_ZhipuStream 只要求可迭代，
        # 这里用 list 替代 SDK Stream 形状，纯为捕捉请求 kwargs 里的墙钟值
        client, _, calls = _make_client(monkeypatch, [[]])
        assert client.stream(model="m", messages=[]) is not None
        expected = min(
            float(config.LLM_REQUEST_TIMEOUT_SECONDS),
            float(config.LLM_STREAM_TIMEOUT_SECONDS),
        )
        assert calls[0]["timeout"] == expected

    def test_complete_attempt_keeps_full_request_budget(self, monkeypatch):
        client, _, calls = _make_client(monkeypatch, [_complete_reply()])
        client.complete(model="m", messages=[])
        assert calls[0]["timeout"] == float(config.LLM_REQUEST_TIMEOUT_SECONDS)
