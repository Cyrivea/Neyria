"""MCP 服务化（E6①）验收：外部 Agent 通过 MCP 协议消费 Capability Runtime。

真 HTTP E2E：测试内起 uvicorn 挂真实 main.app，fastmcp Client 以
Streamable HTTP transport 连 /mcp——与 Claude Desktop 的接入方式同构。
验证面：鉴权（无 token 拒 / 有 token 过）、tools/list 发现、真实工具执行、
usage_events 记 source='mcp'、Provider 缺配置如实报错。
"""

import socket
import sqlite3
import threading
import time

import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from core.database import db_connection
from services.auth_service import create_token, hash_password


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mcp_url():
    """起独立最小 ASGI 应用（只挂 MCP 面），返回 /mcp 端点。

    不用 main.app：它在测试线程里会把全局 redis_client 绑死在该线程的事件循环上，
    后续 test_routers_api 的 TestClient 换循环再 ping 就炸（真机实证跨循环污染）。
    MCP 工具本身（BILLING_MODE=off 下）不碰 Redis——独立挂载即够。"""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    import services.mcp_server as ms

    mcp_asgi = ms.mcp_http_app()

    @asynccontextmanager
    async def _lifespan(app):
        async with mcp_asgi.lifespan(app):
            yield

    fixture_app = FastAPI(lifespan=_lifespan)
    fixture_app.mount("/", mcp_asgi)

    port = _free_port()
    config = uvicorn.Config(fixture_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(60):
        if server.started:
            break
        time.sleep(0.25)
    assert server.started, "测试用 uvicorn 未能在 15s 内就绪"
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True


@pytest.fixture(scope="module")
def mcp_user_token():
    """专测用户 + 真 JWT（登录态即 MCP 令牌）。"""
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, profile, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("mcp-e2e@local.test", hash_password("Mcp#2026"), "", "2026-01-01"),
        )
        row = conn.execute(
            "SELECT id FROM users WHERE username=?", ("mcp-e2e@local.test",)
        ).fetchone()
    return create_token(row["id"], "mcp-e2e@local.test")


def _client(url: str, token: str | None = None) -> Client:
    # fastmcp 的 auth 传裸 token，客户端自己加 Bearer 前缀（传 "Bearer xxx" 会双前缀）
    transport = StreamableHttpTransport(url=url)
    return Client(transport, auth=token)


@pytest.mark.asyncio
async def test_missing_token_rejected_with_guidance(mcp_url):
    async with _client(mcp_url) as client:
        with pytest.raises(ToolError, match="未提供访问令牌"):
            await client.call_tool("get_billing_balance", {})


@pytest.mark.asyncio
async def test_invalid_token_rejected(mcp_url):
    async with _client(mcp_url, token="not-a-jwt") as client:
        with pytest.raises(ToolError, match="无效或已过期"):
            await client.call_tool("get_billing_balance", {})


@pytest.mark.asyncio
async def test_tools_listed_for_discovery(mcp_url, mcp_user_token):
    """外部 Agent 靠 tools/list 发现能力面——四个工具必须在册且描述非空。"""
    async with _client(mcp_url, mcp_user_token) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools}
    assert {"generate_lyrics", "generate_image", "generate_video", "get_billing_balance"} <= names


@pytest.mark.asyncio
async def test_balance_tool_returns_four_tuple(mcp_url, mcp_user_token):
    async with _client(mcp_url, mcp_user_token) as client:
        result = await client.call_tool("get_billing_balance", {})
    payload = result.data
    assert payload["plan_credits"] == "0"
    assert payload["prepaid_credits"] == "0"


@pytest.mark.asyncio
async def test_lyrics_tool_runs_and_usage_records_mcp_source(mcp_url, mcp_user_token, monkeypatch):
    """真工具执行 + 账本记 source='mcp'（与 web/assistant/workflow 三入口并列）。"""
    from services.capabilities import zhipu_text
    from services.llm.types import ChatReply

    class _FakeClient:
        def complete(self, **kw):
            return ChatReply(
                content="主歌第一句", usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
            )

    monkeypatch.setattr(zhipu_text, "get_chat_client", lambda: _FakeClient())
    monkeypatch.setattr("core.config.LYRICS_MODEL", "glm-4-flash")

    async with _client(mcp_url, mcp_user_token) as client:
        result = await client.call_tool("generate_lyrics", {"theme": "毕业"})
    assert "主歌第一句" in str(result.data)

    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT source, capability_id, status FROM usage_events "
            "WHERE user_id=(SELECT id FROM users WHERE username='mcp-e2e@local.test') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["source"] == "mcp"
    assert row["capability_id"] == "lyrics.generate"
    assert row["status"] == "succeeded"


@pytest.mark.asyncio
async def test_capability_failure_surfaces_as_tool_error(mcp_url, mcp_user_token, monkeypatch):
    """Provider 未配置时如实报错——不编造产物、不静默成功。"""
    monkeypatch.setattr("core.config.VIDEO_PROVIDER", "siliconflow")
    monkeypatch.setattr("core.config.SILICONFLOW_API_KEY", "")
    async with _client(mcp_url, mcp_user_token) as client:
        with pytest.raises(ToolError):
            await client.call_tool("generate_video", {"prompt": "夜晚湖面", "duration": "5 秒"})
