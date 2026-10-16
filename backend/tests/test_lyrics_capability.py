"""歌词 Capability Runtime 和 Assistant Block 的单元测试。"""

import asyncio
import json

from agents.tools.registry import capability_map, tools_map
from services.capabilities import CapabilityContext, runtime, use_capability_context


def run(coro):
    return asyncio.run(coro)


def test_lyrics_capability_is_registered_with_hidden_context():
    assert capability_map["generate_lyrics_block"] == "lyrics.generate"
    schema = tools_map["generate_lyrics_block"].args_schema.model_json_schema()
    assert set(schema["properties"]) == {"theme", "style", "mood", "language"}
    assert "context" not in schema["properties"]


def test_assistant_schema_hides_direct_capability_but_keeps_workflow_tools():
    from services.chat import chat_orchestrator

    names = {item["function"]["name"] for item in chat_orchestrator._tools_for_context(False)}
    assert "generate_lyrics_block" not in names
    assert {
        "add_workflow_chains",
        "update_chain_params",
        "read_workflow_state",
        "run_current_workflow",
    } <= names


def _mock_lyrics_executor(runtime_module, monkeypatch, executor):
    entry = runtime_module.CAPABILITY_REGISTRY["lyrics.generate"]
    monkeypatch.setitem(
        runtime_module.CAPABILITY_REGISTRY,
        "lyrics.generate",
        type(entry)(
            capability_id=entry.capability_id,
            display_name=entry.display_name,
            input_model=entry.input_model,
            executor=executor,
        ),
    )


def test_runtime_returns_success_from_mocked_zhipu(monkeypatch):
    _mock_lyrics_executor(runtime, monkeypatch, lambda inputs: f"# {inputs.theme}")
    result = run(
        runtime.run_capability(
            "lyrics.generate",
            {"theme": "夏夜城市", "style": "城市民谣", "mood": "温柔、克制", "language": "中文"},
            context=CapabilityContext(user_id=7, project_id=3, source="standalone"),
        )
    )

    assert result.status == "succeeded"
    assert result.result == {"format": "markdown", "content": "# 夏夜城市"}


def test_runtime_records_usage_on_success(monkeypatch):
    from schemas.capability import LyricsGenerateInput

    recorded = []
    monkeypatch.setattr(
        runtime.usage_repo,
        "insert",
        lambda *args, **kwargs: recorded.append({"args": args, "kwargs": kwargs}) or {"id": 1},
    )

    entry = runtime.CAPABILITY_REGISTRY["lyrics.generate"]
    monkeypatch.setitem(
        runtime.CAPABILITY_REGISTRY,
        "lyrics.generate",
        type(entry)(
            capability_id=entry.capability_id,
            display_name=entry.display_name,
            input_model=LyricsGenerateInput,
            executor=lambda _inputs: {
                "content": "# 夏夜",
                "usage": {"model": "glm-4-flash", "prompt_tokens": 32, "completion_tokens": 40, "total_tokens": 72},
            },
        ),
    )
    result = run(
        runtime.run_capability(
            "lyrics.generate",
            {"theme": "夏夜"},
            context=CapabilityContext(user_id=7, project_id=None, source="standalone"),
        )
    )

    assert result.status == "succeeded"
    assert recorded, "usage repo 应收到一次成功记录"
    args = recorded[0]["args"]
    # (user_id, project_id, capability_id, source, model, prompt, completion, total, ...)
    assert args[0] == 7
    assert args[2] == "lyrics.generate"
    assert args[5] == 32
    assert args[7] == 72
    assert args[8] == "succeeded"


def test_runtime_records_failure_without_tokens(monkeypatch):
    from schemas.capability import LyricsGenerateInput

    recorded = []
    monkeypatch.setattr(
        runtime.usage_repo,
        "insert",
        lambda *args, **kwargs: recorded.append({"args": args, "kwargs": kwargs}) or {"id": 1},
    )
    entry = runtime.CAPABILITY_REGISTRY["lyrics.generate"]
    monkeypatch.setitem(
        runtime.CAPABILITY_REGISTRY,
        "lyrics.generate",
        type(entry)(
            capability_id=entry.capability_id,
            display_name=entry.display_name,
            input_model=LyricsGenerateInput,
            executor=lambda _inputs: (_ for _ in ()).throw(RuntimeError("provider exploded")),
        ),
    )
    result = run(
        runtime.run_capability(
            "lyrics.generate",
            {"theme": "夏夜"},
            context=CapabilityContext(user_id=7, project_id=None, source="standalone"),
        )
    )

    assert result.status == "failed"
    assert recorded, "失败也必须入账，便于之后对账"
    args = recorded[0]["args"]
    assert args[5] == 0 and args[7] == 0  # prompt / total tokens 全是 0
    assert args[8] == "failed"


def test_runtime_hides_provider_error(monkeypatch):
    def fail(_inputs):
        raise RuntimeError("provider secret should not leave runtime")

    _mock_lyrics_executor(runtime, monkeypatch, fail)
    result = run(
        runtime.run_capability(
            "lyrics.generate",
            {"theme": "主题"},
            context=CapabilityContext(user_id=1, project_id=None, source="assistant"),
        )
    )

    assert result.status == "failed"
    assert result.error == "歌词生成失败，请稍后重试"
    assert "secret" not in json.dumps(result.model_dump(), ensure_ascii=False)


def test_assistant_block_passes_hidden_execution_context(monkeypatch):
    captured = {}

    async def fake_run(capability_id, inputs, *, context):
        captured.update(capability_id=capability_id, inputs=inputs, context=context)
        return runtime.CapabilityResult(
            capability_id=capability_id,
            status="succeeded",
            result={"format": "markdown", "content": "歌词"},
        )

    monkeypatch.setattr(runtime, "run_capability", fake_run)
    # 工具模块导入的是函数对象，需要替换它自己的引用来观察上下文。
    import agents.tools.lyrics_block as lyrics_block

    monkeypatch.setattr(lyrics_block, "run_capability", fake_run)
    with use_capability_context(CapabilityContext(user_id=9, project_id=4, source="assistant")):
        raw = run(tools_map["generate_lyrics_block"].ainvoke({"theme": "夏夜城市"}))

    assert json.loads(raw)["result"]["content"] == "歌词"
    assert captured["context"] == CapabilityContext(user_id=9, project_id=4, source="assistant")
