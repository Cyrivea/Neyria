"""MCP 服务化（E6①）：把 Capability Runtime 暴露成 Model Context Protocol 工具面。

一句话定位：同一个 Capability Runtime，第三个消费者——
1. Web 用户（HTTP 路由，FastAPI）
2. 自家 ReAct 助手（agents/tools 注册表）
3. **外部 Agent（MCP 协议，本文件）**

Claude Desktop / Cursor / 任意 MCP 客户端配置本服务后，它们的模型可以直接调用
`generate_lyrics` / `generate_image` / `generate_video` / `get_billing_balance`——
计费照走 Neyria 账本（usage_events.source='mcp'），资产落在 Neyria 库。

鉴权：复用平台 JWT。MCP 客户端在配置里带 `Authorization: Bearer <token>` 头，
工具执行时从 HTTP 请求头取 token 解出 user；无 token / 坏 token 一律拒——
错误消息告诉调用方去 Neyria 登录拿 token，绝不静默放行。

挂载：main.py 以 lifespan 集成（FastMCP 要求把它的 lifespan 接进父应用），
最终端点 `POST /mcp`（streamable HTTP transport，stateless 模式——每请求独立，
不依赖会话，配置最简单）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from jose import JWTError, jwt

from core.config import ALGORITHM, SECRET_KEY
from schemas.capability import (
    CapabilityResult,
    ImageGenerateInput,
    LyricsGenerateInput,
    VideoGenerateInput,
)
from services.capabilities import CapabilityContext, run_capability

logger = logging.getLogger("mcp.server")

INSTRUCTIONS = """Neyria 音乐创作平台的工具面：生成歌词 / 封面 / 视频，查询积分余额。
所有生成类工具是真实付费操作（按平台价目表计积分）。需要画面参考时，
视频工具的 source_asset 可传 Neyria 资产路径（/api/assets/xxx.jpg）。"""

mcp = FastMCP(name="Neyria", instructions=INSTRUCTIONS)


def _current_user_id() -> int:
    """从 MCP 请求头解出用户。无/坏 token 拒绝并给出可操作的指引。"""
    from fastmcp.server.dependencies import get_http_headers

    headers = get_http_headers()
    raw = headers.get("authorization") or ""
    token = raw.removeprefix("Bearer ").strip()
    if not token:
        raise ToolError(
            "未提供访问令牌：请在 MCP 客户端配置 Authorization 头（Bearer <Neyria 登录 token>）"
        )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError) as exc:
        raise ToolError("访问令牌无效或已过期：请重新登录 Neyria 获取 token") from exc


def _run_capability_via_mcp(capability_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """同步工具壳里跑 async 的 run_capability：新线程 + 新事件循环。"""
    user_id = _current_user_id()
    context = CapabilityContext(user_id=user_id, project_id=None, source="mcp")
    result = _run_in_thread(capability_id, inputs, context)
    if result.status != "succeeded":
        raise ToolError(result.error or f"能力执行失败（{result.status}）")
    payload: dict[str, Any] = {"content": (result.result or {}).get("content")}
    asset_id = (result.result or {}).get("asset_id")
    if asset_id:
        payload["asset_id"] = asset_id
    return payload


def _run_in_thread(capability_id: str, inputs: dict, context: CapabilityContext) -> CapabilityResult:
    import concurrent.futures

    async def _call() -> CapabilityResult:
        return await run_capability(capability_id, inputs, context=context)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _call()).result()


# ---------- 工具面（4 个）：与 Web 面同 schema 同校验 ----------


@mcp.tool
def generate_lyrics(
    theme: str,
    style: str = "流行抒情",
    mood: str = "温柔、克制",
    language: str = "中文",
) -> dict:
    """根据主题生成一首完整歌词（Neyria 平台真实付费能力）。"""
    inputs = LyricsGenerateInput(theme=theme, style=style, mood=mood, language=language)
    return _run_capability_via_mcp("lyrics.generate", inputs.model_dump())


@mcp.tool
def generate_image(
    prompt: str,
    style: str = "电影概念艺术",
    ratio: str = "16:9",
    palette: str = "深蓝与紫色",
) -> dict:
    """生成一张歌曲封面/概念图（支持 16:9 / 1:1 / 9:16 / 4:3）。"""
    inputs = ImageGenerateInput(prompt=prompt, style=style, ratio=ratio, palette=palette)
    return _run_capability_via_mcp("image.generate", inputs.model_dump())


@mcp.tool
def generate_video(
    prompt: str,
    style: str = "电影写实",
    ratio: str = "16:9",
    duration: str = "5 秒",
    source_asset: str | None = None,
) -> dict:
    """生成一段短视频。source_asset 传 Neyria 资产路径（/api/assets/xx.jpg）时为图生视频。"""
    inputs = VideoGenerateInput(
        prompt=prompt, style=style, ratio=ratio, duration=duration, source_asset=source_asset
    )
    return _run_capability_via_mcp("video.generate", inputs.model_dump())


@mcp.tool
def get_billing_balance() -> dict:
    """查询当前用户积分余额（套餐/预付、可用/预占四元组 + 流水条数）。"""
    import concurrent.futures


    user_id = _current_user_id()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        balance = pool.submit(asyncio.run, _balance(user_id)).result()
    return {
        "plan_credits": balance["plan_credits"],
        "prepaid_credits": balance["prepaid_credits"],
        "plan_reserved_credits": balance["plan_reserved_credits"],
        "prepaid_reserved_credits": balance["prepaid_reserved_credits"],
        "recent_ledger_count": len(balance.get("recent_ledger") or []),
    }


async def _balance(user_id: int) -> dict:
    from services import billing_service

    return await asyncio.to_thread(billing_service.get_balance, user_id)


def mcp_http_app():
    """给 main.py 挂载用的 ASGI 应用（内部路径 /mcp，需接入 lifespan）。"""
    return mcp.http_app(path="/mcp", stateless_http=True)
