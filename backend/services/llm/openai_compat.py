"""OpenAI 兼容网关 → ChatClient 协议的适配器（Kunbot/任何 OpenAI 形状的中转站）。

与 zhipu.py 的关系：一致对外（同一套 ChatClient 协议、同一套重试/分桶），
全域唯一多出来的认知是「OpenAI SSE chunk 形状」——就在这一个文件里。

多出来的规矩（gatekeeper 特有）：
- 网关会随时把路由重指到更新的模型；调用方传的 model 不在 /v1/models 清单里时，
  不硬撞 —— 自动落到清单第一条并记 WARNING（讹错一个名字比全栈 404 更伤人）。
- 建流阶段拿到非 2xx 状态码 → HttpStatusFailure 交给重试层分桶；
  流读到一半断开的错误向上原样传播（编排器有「中断收尸」兜底）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx

from core import config
from services.llm import retry as _retry
from services.llm.types import ChatReply, LLMStream, StreamDelta, ToolCallDelta

logger = logging.getLogger(__name__)


def _sleep(seconds: float) -> None:
    """独立睡眠点（测试可打桩到零）。"""
    import time

    time.sleep(seconds)


class _OpenAISSEStream:
    """把 OpenAI 风格的 chat.completions 流式响应归一化成 StreamDelta 流。

    建流（拿到响应头）发生在 open_fn 被调用时；真正的 SSE 消费发生在迭代期间。
    close() 供“客户端断开时停掉上游连接”——不停 = 上游继续生成继续计费。
    """

    def __init__(self, resp: httpx.Response, client: httpx.Client, ctx: Any) -> None:
        self._resp = resp
        self._client = client  # 持有句柄，防止迭代期间被回收
        # 关键坑（真机实证）：stream() 的上下文管理器对象必须跟流一起存活，否则
        # __enter__ 返回后 ctx 被 GC，httpx 自动 close——迭代时报 StreamClosed。
        self._ctx = ctx

    def __iter__(self) -> Iterator[StreamDelta]:
        try:
            for line in self._resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue  # 坏行只丢当行，不断流（与聊天 SSE 的 B4 语义一致）
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                calls = delta.get("tool_calls") or []
                # 推理模型的思考增量（本网关的 GLM-5.3 写作 reasoning；OpenAI 面那一系写作
                # reasoning_content）（不保证与正文同片）。不上屏，只作为「上游还活着」
                # 的心跳卖给编排层——前端空闲看门狗收到就不杀活流。
                has_reasoning = bool(delta.get("reasoning") or delta.get("reasoning_content"))
                if has_reasoning and not delta.get("content") and not calls:
                    yield StreamDelta(reasoning=True)
                    continue
                yield StreamDelta(
                    content=delta.get("content") or "",
                    tool_calls=[
                        ToolCallDelta(
                            index=int(c.get("index") or 0),
                            id=c.get("id"),
                            name=(c.get("function") or {}).get("name"),
                            arguments=(c.get("function") or {}).get("arguments") or "",
                        )
                        for c in calls
                    ],
                )
        finally:
            self.close()

    def close(self) -> None:
        try:
            self._resp.close()
            self._ctx.__exit__(None, None, None)
        except Exception:  # 关不干净不影响主链，留个痕
            logger.debug("上游 SSE 连接关闭失败", exc_info=True)


class OpenAICompatChatClient:
    """通用 OpenAI 兼容 ChatClient；鉴权 = Bearer key，HTTP = httpx 直调。"""

    def __init__(self, api_key: str, base_url: str) -> None:
        self._apitimeout = float(config.LLM_REQUEST_TIMEOUT_SECONDS)
        self._base = base_url.rstrip("/")
        self._models_cache: list[str] | None = None
        self._client = httpx.Client(
            base_url=self._base,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    # ---------- 模型清单与回退 ----------

    def _hosted_models(self) -> list[str]:
        """GET /v1/models 拿网关注前挂着的模型清单（带一次性缓存；失败就当没问）。"""
        if self._models_cache is not None:
            return self._models_cache
        try:
            resp = self._client.get("/models", timeout=min(10.0, self._apitimeout))
            resp.raise_for_status()
            data = resp.json().get("data") or []
            self._models_cache = [m["id"] for m in data if isinstance(m, dict) and m.get("id")]
        except Exception as exc:
            logger.debug(
                "模型清单拉取失败（跳过发现，后续照常直传）",
                extra={"evt": "llm_models_discovery_failed", "error_type": type(exc).__name__},
            )
            self._models_cache = []
        return self._models_cache

    def _resolve_model(self, requested: str) -> str:
        """要求的名字没被网关注册时回退第一条——网关随时改挂，硬撞 404 伤全栈。"""
        hosted = self._hosted_models()
        if hosted and requested not in hosted:
            logger.warning(
                "请求的模型未在网关注册，已落到清单首项",
                extra={"evt": "llm_model_fallback", "requested": requested, "used": hosted[0]},
            )
            return hosted[0]
        return requested or (hosted[0] if hosted else requested)

    # ---------- 协议入口 ----------

    def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> LLMStream:
        payload: dict[str, Any] = {
            "model": self._resolve_model(model),
            "messages": messages,
            "stream": True,
        }
        # 空 tools 数组在部分网关（实测 kunbot/vLLM）上直接 400——只在真实有面可传时才带
        if tools:
            payload["tools"] = tools
            # tool_choice 脱离了 tools 是非法组合，多数网关同样拒
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice

        def establish():
            timeout = min(self._apitimeout, float(config.LLM_STREAM_TIMEOUT_SECONDS))
            ctx = self._client.stream(
                "POST", "/chat/completions", json=payload, timeout=timeout
            )
            try:
                resp = ctx.__enter__()
            except Exception:
                logger.debug(
                    "建流抛异常：%s %s hdrs=%s",
                    type(ctx).__name__,
                    payload.get("model"),
                    dict(self._client.headers),
                )
                ctx.__exit__(None, None, None)  # 建流阶段即失败：记得把上下文还给连接池
                raise
            if resp.status_code >= 400:
                body = b"".join(resp.iter_bytes()).decode("utf-8", "replace")
                status = resp.status_code
                # 网关只会回「$ERROR」之类的空话，失败时把载荷解剖进诊断日志
                #（只进日志不进用户面；request 附属品不保证存在，诊断信息有错就略）
                req_obj = getattr(resp, "request", None)
                req_headers = getattr(req_obj, "headers", None) or {}
                logger.error(
                    "网关拒绝 chat/completions：%s | model=%s tools=%d tool_choice=%s body=%s auth_has=%s",
                    getattr(req_obj, "url", "?"),
                    payload.get("model"),
                    len(payload.get("tools") or []),
                    payload.get("tool_choice"),
                    body[:300],
                    any(str(hk).lower() == "authorization" for hk in req_headers),
                )
                ctx.__exit__(None, None, None)
                raise _retry.HttpStatusFailure(status, body)
            return ctx, resp

        ctx, resp = _retry.call_with_retry("stream", establish, sleep_fn=_sleep, log=logger)
        return _OpenAISSEStream(resp, self._client, ctx)

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> ChatReply:
        payload: dict[str, Any] = {
            "model": self._resolve_model(model),
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        def call():
            resp = self._client.post(
                "/chat/completions", json=payload, timeout=self._apitimeout
            )
            if resp.status_code >= 400:
                raise _retry.HttpStatusFailure(resp.status_code, resp.text)
            return resp.json()

        data = _retry.call_with_retry("complete", call, sleep_fn=_sleep, log=logger)
        choices = data.get("choices") or []
        if not choices:
            return ChatReply(content=None)
        message = choices[0].get("message") or {}
        calls = message.get("tool_calls") or []
        usage = data.get("usage")
        return ChatReply(
            content=message.get("content"),
            usage={
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            }
            if usage
            else None,
            tool_calls=[
                ToolCallDelta(
                    index=int(c.get("index") or 0),
                    id=c.get("id"),
                    name=(c.get("function") or {}).get("name"),
                    arguments=(c.get("function") or {}).get("arguments") or "",
                )
                for c in calls
            ],
        )
