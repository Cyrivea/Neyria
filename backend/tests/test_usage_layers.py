"""I2 用量采集收尾：token 分层（cached/reasoning）与 Provider 请求 id 全链贯通。

链路断言点：LLM 适配器提取 → 歌词能力透传 → usage_repo 落库 → runtime 结算喂分层价。
"""

import pytest

# ---------- 迁移：三列存在 ----------

def test_m018_columns_present():

    from core.database import db_connection

    with db_connection() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(usage_events)")]
    assert "cached_tokens" in cols
    assert "reasoning_tokens" in cols
    assert "provider_request_id" in cols


# ---------- openai_compat：details + id 提取 ----------

def test_openai_compat_complete_extracts_token_layers(monkeypatch):
    from services.llm import openai_compat as oc

    client = oc.OpenAICompatChatClient(api_key="sk-x", base_url="http://llm.test/v1")

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "id": "req-abc-123",
                "choices": [{"message": {"content": "你好"}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                    "prompt_tokens_details": {"cached_tokens": 60},
                    "completion_tokens_details": {"reasoning_tokens": 30},
                },
            }

        @property
        def headers(self):
            return {}

    monkeypatch.setattr(
        client._client, "post", lambda *a, **kw: _Resp(), raising=False
    )
    reply = client.complete(model="m", messages=[{"role": "user", "content": "hi"}])
    assert reply.usage["cached_tokens"] == 60
    assert reply.usage["reasoning_tokens"] == 30
    assert reply.usage["prompt_tokens"] == 100
    assert reply.request_id == "req-abc-123"


def test_openai_compat_complete_without_details_keeps_zeros(monkeypatch):
    """Provider 不给 details 不捏造：分层为 0、request_id 为 None。"""
    from services.llm import openai_compat as oc

    client = oc.OpenAICompatChatClient(api_key="sk-x", base_url="http://llm.test/v1")

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

    monkeypatch.setattr(client._client, "post", lambda *a, **kw: _Resp(), raising=False)
    reply = client.complete(model="m", messages=[{"role": "user", "content": "hi"}])
    assert reply.usage["cached_tokens"] == 0
    assert reply.usage["reasoning_tokens"] == 0
    assert reply.request_id is None


# ---------- zhipu_text：透传 ----------

def test_lyrics_usage_passes_layers_through(monkeypatch):
    import services.capabilities.zhipu_text as zt
    from services.llm.types import ChatReply

    class _FakeClient:
        def complete(self, **kw):
            return ChatReply(
                content="第一句歌词",
                usage={
                    "prompt_tokens": 90,
                    "completion_tokens": 40,
                    "total_tokens": 130,
                    "cached_tokens": 50,
                    "reasoning_tokens": 20,
                },
                request_id="req-zp-777",
            )

    monkeypatch.setattr(zt, "get_chat_client", lambda: _FakeClient())
    from schemas.capability import LyricsGenerateInput

    out = zt.generate_lyrics_text(LyricsGenerateInput(theme="海边"))
    assert out["usage"]["cached_tokens"] == 50
    assert out["usage"]["reasoning_tokens"] == 20
    assert out["usage"]["request_id"] == "req-zp-777"


# ---------- usage_repo：落库 ----------

def test_usage_repo_persists_layers(tmp_path):
    from core import database

    old = database.DB_FILE
    database.DB_FILE = tmp_path / "u.db"
    try:
        from core.migrations import run_migrations

        run_migrations()
        with database.db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, profile, created_at) "
                "VALUES ('u1@local.test', 'x', '', '2026-01-01')"
            )
        from repositories import usage_repo

        row = usage_repo.insert(
            user_id=1, project_id=None, capability_id="lyrics.generate",
            source="standalone", model="glm-4-flash",
            prompt_tokens=100, completion_tokens=50, total_tokens=150,
            status="succeeded", error=None, duration_ms=123.0,
            created_at="2026-10-29T00:00:00",
            cached_tokens=60, reasoning_tokens=30, provider_request_id="req-x-1",
        )
        assert row["cached_tokens"] == 60
        assert row["reasoning_tokens"] == 30
        assert row["provider_request_id"] == "req-x-1"
    finally:
        database.DB_FILE = old


# ---------- runtime 结算：分层喂给 billing ----------

@pytest.mark.asyncio
async def test_settle_passes_layers_to_billing(monkeypatch):
    import services.capabilities.runtime as rt

    captured: dict = {}

    def fake_settle(reference, model, prompt, completion, **kw):
        captured.update(dict(reference=reference, model=model, prompt=prompt, completion=completion, **kw))
        return __import__("decimal").Decimal("1")

    monkeypatch.setattr(rt.billing_service, "settle", fake_settle)
    from schemas.capability import LyricsGenerateInput
    from services.capabilities.runtime import CapabilityEntry

    entry = CapabilityEntry(
        capability_id="lyrics.generate", display_name="词",
        input_model=LyricsGenerateInput,
        executor=lambda i: {}, billing_model=lambda: "glm-4-flash",
        billing_estimate=lambda: __import__("decimal").Decimal("5"),
    )
    usage = {
        "model": "glm-4-flash", "prompt_tokens": 100, "completion_tokens": 50,
        "cached_tokens": 60, "reasoning_tokens": 30,
    }
    await rt._settle_reservation("ref-1", "lyrics.generate", entry, usage=usage, usage_event_id=9)
    assert captured["cached_tokens"] == 60
    assert captured["reasoning_tokens"] == 30
