"""智谱（zhipuai SDK）→ ChatClient 协议的适配器。

全仓唯一允许认出 ``chunk.choices[0].delta`` 形状的地方（C12 之前这种娇连散在
编排骨架里三处）；SDK 升级/换模型只动这一个文件。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from services.llm.types import ChatReply, LLMStream, StreamDelta, ToolCallDelta


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
        from zhipuai import ZhipuAI

        self._sdk = ZhipuAI(api_key=api_key)

    def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> LLMStream:
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return _ZhipuStream(self._sdk.chat.completions.create(**kwargs))

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> ChatReply:
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
        if tools is not None:
            kwargs["tools"] = tools
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = self._sdk.chat.completions.create(**kwargs)
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
