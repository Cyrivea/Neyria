"""Capability Runtime：统一承接用户直接运行、Assistant 和未来 Workflow。

能力清单收在一张注册表里：每条 entry 给出输入 Schema、执行器和对外展示名。
新增 Block = 在 ``CAPABILITY_REGISTRY`` 里追加一条，路由/工具/日志都复用。
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from core import config
from repositories import usage_repo
from schemas.capability import CapabilityResult
from services import billing_service
from services import provider_cost as provider_cost_service
from services.capabilities.zhipu_text import ModelNotConfiguredError

logger = logging.getLogger("capabilities")


@dataclass(frozen=True)
class CapabilityContext:
    user_id: int
    project_id: int | None
    source: str
    # 本轮用户原话：供工具做上下文敏感的安全检查（如注入式清空）
    user_message: str = ""
    # WF1-③：workflow 运行时传入 run_id，产物资产才能对账到【哪次运行出的】；
    # assistant/standalone 路径无归属保持 None（删除档案馆运行时才不影响别的产物）
    run_id: str | None = None


@dataclass(frozen=True)
class CapabilityEntry:
    capability_id: str
    display_name: str
    input_model: type[BaseModel]
    executor: Callable[[BaseModel], Any]
    # 懒读 config：测试里 monkeypatch 环境变量也生效
    billing_model: Callable[[], str] = lambda: ""
    billing_estimate: Callable[[], Decimal] = field(default=lambda: Decimal("0"))


_capability_context: contextvars.ContextVar[CapabilityContext | None] = contextvars.ContextVar(
    "capability_context", default=None
)


def current_capability_context() -> CapabilityContext:
    return _capability_context.get() or CapabilityContext(user_id=0, project_id=None, source="assistant")


@contextlib.contextmanager
def use_capability_context(context: CapabilityContext) -> Iterator[None]:
    """在一次工具调用期间绑定隐藏的用户/项目上下文。"""
    token = _capability_context.set(context)
    try:
        yield
    finally:
        _capability_context.reset(token)


async def _record_usage(
    capability_id: str,
    context: CapabilityContext,
    *,
    model: str | None,
    status: str,
    error: str | None,
    duration_ms: float,
    usage: dict | None = None,
) -> int | None:
    """把一次能力调用写入账本。账本落库失败不能阻塞生成主链路。"""
    if context.user_id is None:
        return None
    try:
        event = await asyncio.to_thread(
            usage_repo.insert,
            context.user_id,
            context.project_id,
            capability_id,
            context.source,
            model,
            int(usage["prompt_tokens"]) if usage else 0,
            int(usage["completion_tokens"]) if usage else 0,
            int(usage["total_tokens"]) if usage else 0,
            status,
            error[:500] if error else None,
            duration_ms,
            datetime.now(UTC).isoformat(),
        )
        event_id = int(event["id"])
        # I3：估算上游成本挂到账本（未收录模型返回 None → 列保持 NULL，宁缺不造假）；
        # 估算/写库失败只记日志，绝不阻塞生成主链。
        provider_cost = provider_cost_service.estimate_cost_micro(
            model,
            int(usage["prompt_tokens"]) if usage else 0,
            int(usage["completion_tokens"]) if usage else 0,
        )
        if provider_cost is not None:
            await asyncio.to_thread(usage_repo.attach_provider_cost, event_id, provider_cost)
        return event_id
    except Exception as exc:
        logger.warning(
            "用量记录写不进入处： %s",
            type(exc).__name__,
            extra={"evt": "usage_record_failed", "capability": capability_id},
        )
        return None


async def _release_reservation(reservation_ref: str | None, reason: str) -> None:
    """失败/不可用路径：整笔退回预占。出错只能记日志，不能抛上去打断主链路。"""
    if reservation_ref is None:
        return
    try:
        await asyncio.to_thread(billing_service.release, reservation_ref, reason)
    except Exception as exc:
        logger.warning(
            "计费预占退回失败（额度会在 sweep 中回收）：%s: %s",
            type(exc).__name__,
            exc,
            extra={"evt": "billing_release_failed", "reference_id": reservation_ref},
        )


async def _settle_reservation(
    reservation_ref: str | None,
    capability_id: str,
    entry: CapabilityEntry,
    *,
    usage: dict | None,
    usage_event_id: int | None,
) -> None:
    """成功路径：按真实 token（文本）/固定每次调用价（图像）结算，差额自动退/补。"""
    if reservation_ref is None:
        return
    try:
        await asyncio.to_thread(
            billing_service.settle,
            reservation_ref,
            (usage or {}).get("model") or entry.billing_model(),
            int((usage or {}).get("prompt_tokens", 0)),
            int((usage or {}).get("completion_tokens", 0)),
            usage_event_id=usage_event_id,
        )
    except Exception as exc:
        logger.warning(
            "计费结算失败（额度会留在预占状态，由 sweep 回收）：%s: %s",
            type(exc).__name__,
            exc,
            extra={"evt": "billing_settle_failed", "capability": capability_id, "reference_id": reservation_ref},
        )


def _lyrics_executor(inputs: BaseModel) -> dict:
    """真实 Provider 返回 {"content", "usage"}，Runtime 中心记录用量。"""
    from schemas.capability import LyricsGenerateInput
    from services.capabilities.zhipu_text import generate_lyrics_text

    assert isinstance(inputs, LyricsGenerateInput)
    return generate_lyrics_text(inputs)


def _image_executor(inputs: BaseModel) -> dict:
    """真实图像 Provider（SiliconFlow Kolors）。没配 KEY 仍会走 ModelNotConfiguredError 支路。"""
    from schemas.capability import ImageGenerateInput
    from services.capabilities.siliconflow_image import generate_image

    assert isinstance(inputs, ImageGenerateInput)
    return generate_image(inputs)


def _video_executor(inputs: BaseModel) -> dict:
    """真实视频 Provider（SiliconFlow Wan2.2-T2V）。阻塞轮询 1~5 分钟，依赖调用方在线程池里跑。"""
    from schemas.capability import VideoGenerateInput
    from services.capabilities.siliconflow_video import generate_video

    assert isinstance(inputs, VideoGenerateInput)
    return generate_video(inputs)


def _register() -> dict[str, CapabilityEntry]:
    from schemas.capability import ImageGenerateInput, LyricsGenerateInput, VideoGenerateInput

    return {
        "lyrics.generate": CapabilityEntry(
            capability_id="lyrics.generate",
            display_name="歌词",
            input_model=LyricsGenerateInput,
            executor=_lyrics_executor,
            billing_model=lambda: config.LYRICS_MODEL,
            billing_estimate=lambda: Decimal(config.BILLING_LYRICS_RESERVE_CREDITS),
        ),
        "image.generate": CapabilityEntry(
            capability_id="image.generate",
            display_name="图像",
            input_model=ImageGenerateInput,
            executor=_image_executor,
            billing_model=lambda: config.IMAGE_MODEL,
            billing_estimate=lambda: Decimal(config.BILLING_IMAGE_RESERVE_CREDITS),
        ),
        "video.generate": CapabilityEntry(
            capability_id="video.generate",
            display_name="视频",
            input_model=VideoGenerateInput,
            executor=_video_executor,
            billing_model=lambda: config.VIDEO_MODEL,
            billing_estimate=lambda: Decimal(config.BILLING_VIDEO_RESERVE_CREDITS),
        ),
    }


CAPABILITY_REGISTRY: dict[str, CapabilityEntry] = _register()


async def _write_asset(asset: dict, context: CapabilityContext) -> None:
    """把一次 capability 产出的资产插进资产表（不阻塞主链路）。"""
    try:
        from repositories import asset_repo

        # 按扩展名推导资产种类：视频 Block 与图像 Block 共用这条落库路径
        suffix = Path(asset["filename"]).suffix.lower()
        kind, content_type = (
            ("video", "video/mp4") if suffix == ".mp4" else ("image", "image/png")
        )

        await asyncio.to_thread(
            asset_repo.insert,
            user_id=context.user_id,
            project_id=context.project_id,
            run_id=context.run_id,
            capability_id=asset["capability_id"],
            kind=kind,
            filename=asset["filename"],
            content_type=content_type,
            byte_size=0,
            prompt_snapshot=asset.get("prompt_snapshot"),
            usage_event_id=None,
            created_at=datetime.now(UTC).isoformat(),
        )
    except Exception as exc:
        logger.warning(
            "资产登记失败（不阻塞生成）",
            extra={"evt": "asset_register_failed", "error_type": type(exc).__name__},
        )


def _truncate_overlong_strings(
    model_cls: type[BaseModel], inputs: dict[str, Any]
) -> BaseModel | None:
    """超长字符串入参按 Field(max_length) 截断后重校验。

    背景（D 类真机实证）：下游模型的字符上限（如 Kolors prompt 300 字）碰撞
    上游长产物（整段歌词）；撞上限不该让整链失败——截断 = 允许任性生成但保留
    边界物理约束，截断事实记 WARNING 日志供调优回溯。截完仍不过（空串/其它
    字段错）返回 None，由调用方按原口径报「参数不符合平台要求」。
    """
    from annotated_types import MaxLen

    patched = dict(inputs)
    any_change = False
    for name, field_info in model_cls.model_fields.items():
        value = patched.get(name)
        if not isinstance(value, str):
            continue
        max_len = next(
            (meta.max_length for meta in field_info.metadata if isinstance(meta, MaxLen)),
            None,
        )
        if max_len is not None and len(value) > max_len:
            patched[name] = value[:max_len]
            any_change = True
    if not any_change:
        return None
    try:
        return model_cls.model_validate(patched)
    except ValidationError:
        return None


async def run_capability(
    capability_id: str,
    inputs: dict[str, Any],
    *,
    context: CapabilityContext,
    idempotency_key: str | None = None,
) -> CapabilityResult:
    """执行一个受控生产能力，Provider 细节不向调用入口泄露。

    安全边界声明（C19-③，有意决策）：capability 输入不过聊天层注入守卫——
    这里是单轮生成（无工具权限、无对话状态、接触不到系统提示以外的机密），
    注入只能影响用户自己这一次的生成结果，爆炸半径≈ 0；
    内容合规（色情/违法 prompt）是另一个范畴，属 Provider 内容安全职责。
    """
    entry = CAPABILITY_REGISTRY.get(capability_id)
    if entry is None:
        return CapabilityResult(capability_id=capability_id, status="failed", error="不支持的生产能力")

    try:
        validated = entry.input_model.model_validate(inputs)
    except ValidationError as first_error:
        # 超长 prompt 宽容截断：上游引用长文本（如整段歌词）会越过下游模型的
        # 字符上限——这不该是让用户错底的坑，截到 max_length 并记日志后继续；
        # 截完还不过（空串/其它字段错）才如实返回「参数不符合平台要求」。
        salvaged = _truncate_overlong_strings(entry.input_model, inputs)
        if salvaged is None:
            return CapabilityResult(
                capability_id=capability_id,
                status="failed",
                error=f"{entry.display_name} 参数不符合平台要求",
            )
        validated = salvaged
        logger.warning(
            "能力入参超长已被截断",
            extra={
                "evt": "capability_input_truncated",
                "capability": capability_id,
                "source": context.source,
                "first_error": str(first_error)[:200],
            },
        )

    logger.info(
        "能力开始执行",
        extra={
            "evt": "capability_started",
            "capability": capability_id,
            "source": context.source,
            "user_id": context.user_id,
            "project_id": context.project_id,
        },
    )
    # 计费钩子：reserve → 执行 → settle / release。任何时候计费代码出错都不许拖垮主链路，
    # 只在 enforce 模式下【积分不足 / 价格未配置】才拒绝执行（在接触上游 Provider 之前）。
    billing_mode = config.BILLING_MODE
    reservation_ref: str | None = None
    if billing_mode in ("track", "enforce"):
        # I12：独立入口的客户端幂等键优先——双击/断网重试携带同一 key，reservation 唯一索引兜底，
        # 生成绝不会执行第二次。assistant/workflow 路径不传 key，每次工具调用都是真·新生成。
        reservation_ref = f"cap:{capability_id}:{context.user_id}:{idempotency_key or uuid.uuid4().hex}"
        try:
            await asyncio.to_thread(
                billing_service.reserve,
                context.user_id,
                reservation_ref,
                entry.billing_estimate(),
            )
        except billing_service.DuplicateReservationError:
            # 重复提交必须传到路由层换 409——吞掉或办好都会让平台替用户多付出一次 Provider 成本
            raise
        except billing_service.InsufficientCreditsError as exc:
            if billing_mode == "enforce":
                logger.warning(
                    "计费拒绝：积分不足",
                    extra={"evt": "billing_rejected", "capability": capability_id, "user_id": context.user_id},
                )
                return CapabilityResult(
                    capability_id=capability_id,
                    status="failed",
                    error=f"积分不足，无法执行{entry.display_name}任务，请充值或升级套餐",
                )
            reservation_ref = None  # track 模式：欠费也不拦，但不记录关联数据
            logger.warning(
                "计费记帐跳过（track 模式欠费）：%s",
                exc,
                extra={"evt": "billing_track_skipped", "capability": capability_id, "user_id": context.user_id},
            )
        except billing_service.PriceNotConfiguredError as exc:
            if billing_mode == "enforce":
                return CapabilityResult(
                    capability_id=capability_id,
                    status="model_unavailable",
                    error=f"{entry.display_name}模型的计费尚未配置，请稍后再试",
                )
            reservation_ref = None
            logger.warning(
                "计费记帐跳过（track 模式无价版）：%s",
                exc,
                extra={"evt": "billing_track_skipped", "capability": capability_id, "user_id": context.user_id},
            )
        except Exception as exc:
            reservation_ref = None
            logger.warning(
                "计费预占失败，不阻塞主链路：%s: %s",
                type(exc).__name__,
                exc,
                extra={"evt": "billing_reserve_failed", "capability": capability_id, "user_id": context.user_id},
            )

    started_at = time.perf_counter()
    try:
        output = await asyncio.to_thread(entry.executor, validated)
    except ModelNotConfiguredError:
        await _release_reservation(reservation_ref, "模型尚未配置")
        await _record_usage(
            capability_id,
            context,
            model=None,
            status="model_unavailable",
            error="模型尚未配置",
            duration_ms=(time.perf_counter() - started_at) * 1000,
        )
        logger.warning(
            "能力模型未配置",
            extra={
                "evt": "capability_model_unavailable",
                "capability": capability_id,
                "source": context.source,
            },
        )
        return CapabilityResult(
            capability_id=capability_id,
            status="model_unavailable",
            error=f"{entry.display_name}模型尚未配置，请稍后再试",
        )
    except Exception as exc:
        await _release_reservation(reservation_ref, f"执行异常：{type(exc).__name__}")
        await _record_usage(
            capability_id,
            context,
            model=None,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            duration_ms=(time.perf_counter() - started_at) * 1000,
        )
        logger.exception(
            "能力执行失败",
            extra={
                "evt": "capability_error",
                "capability": capability_id,
                "source": context.source,
                "error_type": type(exc).__name__,
            },
        )
        return CapabilityResult(
            capability_id=capability_id,
            status="failed",
            error=f"{entry.display_name}生成失败，请稍后重试",
        )

    duration_ms = (time.perf_counter() - started_at) * 1000
    usage = output.get("usage") if isinstance(output, dict) else None
    content = output.get("content", "") if isinstance(output, dict) else output
    usage_event_id = await _record_usage(
        capability_id,
        context,
        model=usage.get("model") if usage else None,
        status="succeeded",
        error=None,
        duration_ms=duration_ms,
        usage=usage,
    )
    await _settle_reservation(
        reservation_ref,
        capability_id,
        entry,
        usage=usage,
        usage_event_id=usage_event_id,
    )

    # 用电资产登记账本：任何 capability 返回的 "asset" 字典会插入 assets 表
    asset = output.get("asset") if isinstance(output, dict) else None
    if asset:
        await _write_asset(asset, context)

    logger.info(
        "能力执行完成",
        extra={
            "evt": "capability_done",
            "capability": capability_id,
            "source": context.source,
            "user_id": context.user_id,
            "project_id": context.project_id,
            "result_chars": len(content or ""),
            "duration_ms": round(duration_ms, 1),
        },
    )
    return CapabilityResult(
        capability_id=capability_id,
        status="succeeded",
        result={"format": "markdown", "content": content},
    )
