"""智谱（zhipuai SDK）→ ChatClient 协议的适配器。

全仓唯一允许认出 ``chunk.choices[0].delta`` 形状的地方（C12 之前这种娇连散在
编排骨架里三处）；SDK 升级/换模型只动这一个文件。

C26 韧性层（关掉 SDK 盲重试，适配器自建、规则见 ``services/llm/retry.py``）：
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
import time
from collections.abc import Iterator
from typing import Any

from core import config
from services.llm import retry as _retry
from services.llm.types import ChatReply, LLMStream, StreamDelta, ToolCallDelta

logger = logging.getLogger(__name__)

# 测试兼容口：存量用例打桩 zhipu._sleep / 读 zhipu.get_failure_buckets / 清 _FAILURE_BUCKETS，
# 实现体已迁入 services/llm/retry.py 供所有适配器共用。绑同一对象，不是拷贝。
_FAILURE_BUCKETS = _retry._FAILURE_BUCKETS
get_failure_buckets = _retry.get_failure_buckets
_classify_error = _retry.classify_error


def _sleep(seconds: float) -> None:
    """独立成名字的 sleep，测试可打桩成零耗时（避免重试测试真等退避）。"""
    time.sleep(seconds)


def _call_with_retry(op: str, fn):
    return _retry.call_with_retry(op, fn, sleep_fn=_sleep, log=logger)


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
        # C26：SDK 盲重试关闭（默认 3 次且无原因日志），重试改由 retry 层接管
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
        usage = getattr(response, "usage", None)
        return ChatReply(
            content=getattr(message, "content", None),
            usage={
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            }
            if usage is not None
            else None,
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
