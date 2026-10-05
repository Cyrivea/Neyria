"""工具执行器（C2 拆分件之一）：安全地执行一次模型请求的工具调用。

合同：无论成功 / 参数不合法被拒 / 执行时抛异常，一律返回 ToolOutcome，
**绝不向外抛异常**——编排器拿到结果单只管转交给模型，流程不分叉；
三种结局的差异只体现在日志里（排障用），不体现在控制流上。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

from agents.tools.registry import capability_map, tools_map
from core import config
from core.exceptions import AppError

logger = logging.getLogger("assistant")

MAX_TOOL_ARG_LENGTH = 500


@dataclass
class ToolOutcome:
    """工具执行的"结果单"。result 是给模型看的文本，ok 用于日志/埋点；

    error_kind（C13）是失败的结构化分类，编排器靠它识别「同错重试」：
    - None：成功
    - "rejected"：参数/报文不合法被拒
    - "business_denied"：平台业务规则拒绝（携带可执行纠正指令）
    - "timeout"：墙钟超时
    - "exception:<Type>"：执行抛异常
    """

    result: str
    ok: bool
    error_kind: str | None = None


def validate_tool_call(func_name: str, func_args: dict) -> str | None:
    """校验模型返回的工具调用，返回错误信息；合法时返回 None。"""
    if func_name not in tools_map:
        return f"不支持的工具: {func_name}"
    if not isinstance(func_args, dict):
        return "工具参数格式错误"
    for value in func_args.values():
        if isinstance(value, str) and len(value) > MAX_TOOL_ARG_LENGTH:
            return "工具参数过长"
    return None


async def execute_tool(func_name: str, func_args_raw: str) -> ToolOutcome:
    """执行一次工具调用；同步工具和生产能力都不会阻塞聊天事件循环。"""
    try:
        func_args = json.loads(func_args_raw)
    except (json.JSONDecodeError, TypeError):
        func_args = None
    error = "工具参数解析失败" if func_args is None else validate_tool_call(func_name, func_args)
    if error:
        logger.warning(
            "工具调用被拒绝: %s",
            error,
            extra={"evt": "tool_rejected", "tool": func_name, "reason": error},
        )
        return ToolOutcome(result=f"工具调用被拒绝：{error}", ok=False, error_kind="rejected")

    started = time.perf_counter()
    try:
        # 防卡死：工具执行必须有墙钟上限。上游 Provider/网络挂起不能让用户永远看着
        # "正在调用 xx"转圈——超时就以一张失败结果单还给模型，由模型向用户如实收同。
        if func_name in capability_map:

            result = await asyncio.wait_for(
                tools_map[func_name].ainvoke(func_args),
                timeout=config.TOOL_TIMEOUT_SECONDS,
            )
        else:

            result = await asyncio.wait_for(
                asyncio.to_thread(tools_map[func_name].invoke, func_args),
                timeout=config.TOOL_TIMEOUT_SECONDS,
            )
    except TimeoutError:
        logger.warning(
            "工具执行超时: %s",
            func_name,
            extra={"evt": "tool_timeout", "tool": func_name, "timeout_s": config.TOOL_TIMEOUT_SECONDS},
        )
        return ToolOutcome(
            result=f"工具 {func_name} 执行超过 {config.TOOL_TIMEOUT_SECONDS} 秒上限已被中断，"
            "请明确告诉用户本次操作超时、请稍后重试或换个问法。",
            ok=False,
            error_kind="timeout",
        )
    except AppError as exc:
        # 业务拒绝（如“引用上游却没连线”）：detail 是给模型的可执行纠正指令，
        # 不能吞掉改成笼统的“失败了重试”——模型看不到原因只会摊手或乱猜。
        logger.info(
            "工具被业务规则拒绝: %s",
            func_name,
            extra={"evt": "tool_rejected_business", "tool": func_name, "detail": exc.detail},
        )
        return ToolOutcome(result=f"操作被平台规则拒绝：{exc.detail}", ok=False, error_kind="business_denied")
    except Exception as exc:
        logger.exception(
            "工具执行失败: %s",
            type(exc).__name__,
            extra={
                "evt": "tool_error",
                "tool": func_name,
                "error_type": type(exc).__name__,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return ToolOutcome(
            result="工具执行失败，请换个方式提问或稍后重试。",
            ok=False,
            error_kind=f"exception:{type(exc).__name__}",
        )

    logger.info(
        "工具调用完成",
        extra={
            "evt": "tool_call",
            "tool": func_name,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "result_chars": len(str(result)),
        },
    )
    return ToolOutcome(result=str(result), ok=True)
