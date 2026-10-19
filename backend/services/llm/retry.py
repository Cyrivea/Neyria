"""LLM 调用的受预算重试层（C26 从 SDK 盲重试回收而来，抽出供所有适配器共用）。

职责：
- 失败分类入桶（timeout/connection/rate_limit/server_error/client_error/unknown），
  进程内累计计数供排障与告警观察（get_failure_buckets）；
- 只有瞬态类可重试，4xx(≠429) 一次失败即抛——重试不会自愈还烧钱；
- 预算与退避全走 core/config（LLM_MAX_RETRIES / LLM_RETRY_BACKOFF_SECONDS）。

`sleep_fn` 让宿主适配器保留自己的可打桩睡眠点（存量测试的打桩位不动）。
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from typing import Any

from core import config

logger = logging.getLogger(__name__)

# 按失败类别分桶的进程内计数——「为什么失败」盲区消除后的最小指标面
_FAILURE_BUCKETS: Counter[str] = Counter()
# 只有瞬态类别值得重试；client_error（4xx≠429）重来也不会自愈
_RETRYABLE_CATEGORIES = frozenset({"timeout", "connection_error", "rate_limit", "server_error"})


def get_failure_buckets() -> dict[str, int]:
    """LLM 调用失败类别累计计数（进程内）。排障/告警观察用。"""
    return dict(_FAILURE_BUCKETS)


class HttpStatusFailure(Exception):
    """非 SDK 化错误信使：给不产出 APIStatusError 的 Provider（httpx 直调）用。

    携带 status_code 供分类器分桶；消息里塞下响回应答掐头，排障时省一次复现。
    """

    def __init__(self, status_code: int, body_preview: str = "") -> None:
        super().__init__(f"upstream HTTP {status_code}: {body_preview[:200]}")
        self.status_code = status_code


def classify_error(exc: BaseException) -> tuple[str, int | None]:
    """异常 → (失败类别, HTTP 状态码）。认识智谱 SDK 形态、httpx 形态和 HttpStatusFailure。"""
    # 通用路径：自定义信使 / 带 status_code 属性的异常 / httpx 超时与连接错
    status = getattr(exc, "status_code", None)
    if status is not None:
        return _bucket_for_status(status), status
    try:  # 智谱 SDK 家族（软依赖：装没装都活着）
        from zhipuai.core._errors import APIConnectionError, APIStatusError, APITimeoutError

        if isinstance(exc, APITimeoutError):
            return "timeout", None
        if isinstance(exc, APIConnectionError):
            return "connection_error", None
        if isinstance(exc, APIStatusError):
            st = getattr(getattr(exc, "response", None), "status_code", None)
            return _bucket_for_status(st), st
    except ImportError:  # pragma: no cover - zhipuai 是主依赖，保底防御
        pass
    import httpx

    if isinstance(exc, httpx.TimeoutException):
        return "timeout", None
    if isinstance(exc, httpx.TransportError):
        return "connection_error", None
    return "unknown", None


def _bucket_for_status(status: int | None) -> str:
    if status == 429:
        return "rate_limit"
    if status is not None and status >= 500:
        return "server_error"
    if status is not None:
        return "client_error"
    return "unknown"


def call_with_retry(
    op: str,
    fn: Callable[[], Any],
    *,
    sleep_fn: Callable[[float], None],
    log: logging.Logger | None = None,
) -> Any:
    """带分类日志的受预算重试执行器：瞬态失败按预算重试，其余一次失败即抛。"""
    logger = log or globals()["logger"]
    max_retries = max(0, config.LLM_MAX_RETRIES)
    for attempt in range(1, max_retries + 2):
        try:
            return fn()
        except Exception as exc:
            category, status = classify_error(exc)
            _FAILURE_BUCKETS[category] += 1
            can_retry = category in _RETRYABLE_CATEGORIES and attempt <= max_retries
            import random

            delay = (
                config.LLM_RETRY_BACKOFF_SECONDS * attempt + random.uniform(0, 0.5)
                if can_retry
                else 0.0
            )
            emit = logger.warning if can_retry else logger.error
            emit(
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
            sleep_fn(delay)
    raise AssertionError("unreachable")  # pragma: no cover
