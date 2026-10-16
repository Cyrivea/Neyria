"""智谱（zhipuai SDK）→ ChatClient 协议的适配器。

全仓唯一允许认出 ``chunk.choices[0].delta`` 形状的地方（C12 之前这种娇连散在
编排骨架里三处）；SDK 升级/换模型只动这一个文件。

C26 韧性层（关掉 SDK 盲重试，适配器自建）：
- SDK 自带指数退避重试的日志只打 ``Retrying request ... in N seconds``，不带
  HTTP 状态码/异常类型——429 限速、5xx、超时、余额不足从日志无法区分；
- 且 SDK 默认 ``max_retries=3``，叠加 120s 请求超时，最坏建流窗口远超前端看门狗，
  典型结果是「前端先掐断，后端最后那次异常反而没人记」（C26 盲区形成机理）。
- 现在：SDK ``max_retries=0``；失败先分类入桶（timeout/connection/rate_limit/
  server_error/client_error/unknown），只重试瞬态类；4xx 撞进即抛（重试不会
  自愈还烧钱）；每次失败都有 evt 级结构化日志，类别累计见 ``get_failure_buckets()``。
"""

from __future__ import annotations

import logging
import random
import time
from collections import Counter
from collections.abc import Callable, Iterator
from typing import Any

from core import config
from services.llm.types import ChatReply, LLMStream, StreamDelta, ToolCallDelta

logger = logging.getLogger(__name__)

# C26：按失败类别分桶的进程内计数（“为什么失败”盲区消除后的最小指标面，
# 配合日志检索即可按类别看趋势，排障/告警都够）
_FAILURE_BUCKETS: Counter[str] = Counter()
# 只有瞬态类别值得重试；client_error（4xx≠429）重来也不会自愈
_RETRYABLE_CATEGORIES = frozenset({"timeout", "connection_error", "rate_limit", "server_error"})


def get_failure_buckets() -> dict[str, int]:
    """LLM 调用失败类别累计计数（进程内）。排障/告警观察用。"""
    return dict(_FAILURE_BUCKETS)


def _classify_error(exc: BaseException) -> tuple[str, int | None]:
    """SDK 异常 → (失败类别, HTTP 状态码)。补上 SDK 日志不带原因的盲区。"""
    from zhipuai.core._errors import APIConnectionError, APIStatusError, APITimeoutError

    if isinstance(exc, APITimeoutError):
        return "timeout", None
    if isinstance(exc, APIConnectionError):
        return "connection_error", None
    if isinstance(exc, APIStatusError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429:
            return "rate_limit", status
        if status is not None and status >= 500:
            return "server_error", status
        if status is not None:
            return "client_error", status
        return "unknown", None
    return "unknown", None


def _sleep(seconds: float) -> None:
    """独立成名字的 sleep，测试可打桩成零耗时（避免重试测试真等退避）。"""
    time.sleep(seconds)


def _call_with_retry(op: str, fn: Callable[[], Any]) -> Any:
    """带分类日志的受预算重试执行器：瞬态失败按预算重试，其余一次失败即抛。"""
    max_retries = max(0, config.LLM_MAX_RETRIES)
    for attempt in range(1, max_retries + 2):
        try:
            return fn()
        except Exception as exc:
            category, status = _classify_error(exc)
            _FAILURE_BUCKETS[category] += 1
            can_retry = category in _RETRYABLE_CATEGORIES and attempt <= max_retries
            delay = config.LLM_RETRY_BACKOFF_SECONDS * attempt + random.uniform(0, 0.5) if can_retry else 0.0
            log = logger.warning if can_retry else logger.error
            log(
                "LLM %s 调用失败 [类别=%s]（第 %d/%d 次）：%s",
                op,
                category,
                attempt,
                max_retries + 1,
                f"{delay:.1f}s 后重试" if can_retry else "已达预算上限，如实向上抛",
                extra={
                    "evt": "llm_attempt_failed",
                    "op": op,
                    "attempt": attempt,
                    "category": category,
                    "status_code": status,
                    "error_type": type(exc).__name__,
                    "retrying": can_retry,
                    "delay_seconds": round(delay, 2),
                },
            )
            if not can_retry:
                raise
            _sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


class _ZhipuStream:
    """包装智谱流式响应，把 SDK chunk 归一化成 StreamDelta。"""

    def __init__(self, raw_stream: Any) -> None:
        self._raw = raw_stream

    def __iter__(self) -> Iterator[StreamDelta]:
        for chunk in self._raw:
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = choices[0].delta
            calls = getattr(delta, "tool_calls", None) or []
            yield StreamDelta(
                content=getattr(delta, "content", None) or "",
                tool_calls=[
                    ToolCallDelta(
                        index=getattr(call, "index", None) or 0,
                        id=getattr(call, "id", None),
                        name=getattr(getattr(call, "function", None), "name", None),
                        arguments=getattr(getattr(call, "function", None), "arguments", None) or "",
                    )
                    for call in calls
                ],
            )

    def close(self) -> None:
        close = getattr(self._raw, "close", None)
        if callable(close):
            close()


class ZhipuChatClient:
    """智谱 ChatClient 实现；api_key 由 factory 注入。"""

    def __init__(self, api_key: str) -> None:
        # 命中在适配器层统一御守：SDK 构造即携带墙钟上限
        from zhipuai import ZhipuAI

        self._apitimeout = float(config.LLM_REQUEST_TIMEOUT_SECONDS)
        # C26：SDK 盲重试关闭（默认 3 次且无原因日志），重试改由 _call_with_retry 接管
        self._sdk = ZhipuAI(api_key=api_key, timeout=self._apitimeout, max_retries=0)

    def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> LLMStream:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            # 流式单次尝试墙钟走独立预算（默认 60s），与前端 60s 空闲看门狗同档：
            # 建流卡死/长空闲都在该窗口内自我了断，不产生「谁也没报错」的悬停
            "timeout": min(self._apitimeout, float(config.LLM_STREAM_TIMEOUT_SECONDS)),
        }
        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        create = lambda: self._sdk.chat.completions.create(**kwargs)  # noqa: E731
        return _ZhipuStream(_call_with_retry("stream", create))

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> ChatReply:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "timeout": self._apitimeout,
        }
        if tools is not None:
            kwargs["tools"] = tools
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        create = lambda: self._sdk.chat.completions.create(**kwargs)  # noqa: E731
        response = _call_with_retry("complete", create)
        choices = getattr(response, "choices", None)
        if not choices:
            return ChatReply(content=None)
        message = choices[0].message
        calls = getattr(message, "tool_calls", None) or []
        return ChatReply(
            content=getattr(message, "content", None),
            tool_calls=[
                ToolCallDelta(
                    index=getattr(call, "index", None) or 0,
                    id=getattr(call, "id", None),
                    name=getattr(getattr(call, "function", None), "name", None),
                    arguments=getattr(getattr(call, "function", None), "arguments", None) or "",
                )
                for call in calls
            ],
        )
