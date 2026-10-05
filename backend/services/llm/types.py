"""LLM Provider 抽象层（C12）的中立类型定义。

设计意图：上层（编排器/意图分类器/追问建议）不再认识任何具体 SDK 的 chunk 形状
（``chunk.choices[0].delta`` 那种娇连写法全部收敛进 Provider 适配器）。
换 Provider = 写一个实现 ``ChatClient`` 协议的适配器 + factory 注册一行，
不动编排器一行代码。

协议刻意取三家（智谱/OpenAI/Claude）的交集：消息列表 + 可选 tools + 流式。文本
增量和工具调用分片在同一条流上交替到达，顺序承载语义。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCallDelta:
    """一次工具调用的流式分片：id/name 先到，arguments 逐段追加；并行调用靠 index 区分。"""

    index: int = 0
    id: str | None = None
    name: str | None = None
    arguments: str = ""


@dataclass
class StreamDelta:
    """一次流式增量：纯文本 XOR 工具调用分片（Provider 极少两者同行，上层按序处理）。"""

    content: str = ""
    tool_calls: list[ToolCallDelta] = field(default_factory=list)


@dataclass
class ChatReply:
    """非流式完整回复：文本 + 可选的工具调用列表（evaluate/scenario 类脚本需要）。"""

    content: str | None
    tool_calls: list[ToolCallDelta] = field(default_factory=list)


class LLMStream(Protocol):
    """可关闭的流：生成被客户端中断时必须能 close 停掉上游，否则持续计费。"""

    def __iter__(self) -> Iterator[StreamDelta]: ...
    def close(self) -> None: ...


class ChatClient(Protocol):
    """聊天模型客户端的最低公约数。"""

    def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> LLMStream: ...

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> ChatReply: ...
