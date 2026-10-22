"""chat_orchestrator 的单元测试（C2+C3）：todo §6.4 要求的五种场景。

用假 client 按剧本回放流式分片，验证编排器的行为契约：
无工具 / 一个工具 / 同轮多工具 / 工具失败不崩流 / 无限请求工具触顶收尾。
"""

import asyncio
import json

import pytest

from services.chat import chat_orchestrator
from services.chat.context_builder import ChatContext
from services.chat.tool_executor import ToolOutcome
from services.llm import ChatReply, StreamDelta, ToolCallDelta

# ---------- 假流式分片（C12 后：直接构造抽象层中立类型） ----------


def content_stream(*texts):
    return [StreamDelta(content=t) for t in texts]


def tool_call_stream(*calls):
    """calls: (index, id, name, arguments)；arguments 拆两段模拟流式分片。"""
    chunks = []
    for index, call_id, name, arguments in calls:
        half = len(arguments) // 2
        chunks.append(
            StreamDelta(tool_calls=[ToolCallDelta(index=index, id=call_id, name=name, arguments=arguments[:half])])
        )
        chunks.append(StreamDelta(tool_calls=[ToolCallDelta(index=index, arguments=arguments[half:])]))
    return chunks


class FakeStream:
    """C12 后的测试流：LLMStream 协议（可迭代 + close）。"""

    def __init__(self, deltas):
        self._deltas = deltas
        self.closed = False

    def __iter__(self):
        return iter(self._deltas)

    def close(self):
        self.closed = True


class FakeClient:
    """C12 后的测试双：实现 ChatClient 协议（stream/complete），不再仿智谱 SDK 链。
    按剧本逐次回放流；记录每次请求的 kwargs 供断言。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.streams: list[FakeStream] = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        handle = FakeStream(self.script.pop(0))
        self.streams.append(handle)
        return handle

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return ChatReply(content="[]")



# ---------- 公共脚手架 ----------


@pytest.fixture
def persisted(monkeypatch):
    saved = []
    monkeypatch.setattr(
        chat_orchestrator,
        "append_message",
        lambda user_id, role, content, project_id=None, attachments=None, citations=None: saved.append(
            (role, content)
        ),
    )

    async def fake_context(user_id, message, project_id=None, attachments=None):
        return ChatContext(
            messages=[{"role": "system", "content": "SYS"}, {"role": "user", "content": message}],
            clean_history=[{"role": "user", "content": message}],
            persist_text=message,
            attachments=[],
        )

    monkeypatch.setattr(chat_orchestrator, "build_chat_context", fake_context)
    return saved


def run_chat(monkeypatch, script):
    fake = FakeClient(script)
    monkeypatch.setattr(chat_orchestrator, "client", fake)

    async def collect():
        events = []
        async for raw in chat_orchestrator.stream_chat({"id": 1}, "hi"):
            events.append(json.loads(raw.removeprefix("data: ").strip()))
        return events

    return fake, asyncio.run(collect())


def events_of(events, type_):
    return [e for e in events if e["type"] == type_]


# ---------- 五种场景 ----------


def test_no_tool(monkeypatch, persisted):
    fake, events = run_chat(monkeypatch, [content_stream("你", "好")])

    assert "".join(e["content"] for e in events_of(events, "content")) == "你好"
    assert events_of(events, "tool") == []
    done = events_of(events, "done")[0]
    assert done["tool_used"] is None
    assert done["history"][-1] == {"role": "assistant", "content": "你好"}
    assert len(fake.calls) == 1
    assert "tools" in fake.calls[0]  # 首轮必须把工具亮给模型
    assert persisted == [("user", "hi"), ("assistant", "你好")]


def test_done_includes_rag_citations(monkeypatch, persisted):
    async def citation_context(user_id, message, project_id=None, attachments=None):
        return ChatContext(
            messages=[{"role": "system", "content": "RAG"}, {"role": "user", "content": message}],
            clean_history=[{"role": "user", "content": message}],
            persist_text=message,
            attachments=[],
            citations=[
                {
                    "source": "guide.md",
                    "document_id": "d" * 32,
                    "chunk_index": 1,
                    "chunk_count": 3,
                    "distance": 0.42,
                }
            ],
            has_knowledge_context=True,
        )

    monkeypatch.setattr(chat_orchestrator, "build_chat_context", citation_context)
    _fake, events = run_chat(monkeypatch, [content_stream("有依据的回答")])

    done = events_of(events, "done")[0]
    assert done["citations"][0]["source"] == "guide.md"
    assert done["history"][-1]["citations"][0]["chunk_index"] == 1


def test_private_context_disables_web_fallback(monkeypatch, persisted):
    async def private_context(user_id, message, project_id=None, attachments=None):
        return ChatContext(
            messages=[{"role": "system", "content": "私有 RAG 资料"}, {"role": "user", "content": message}],
            clean_history=[{"role": "user", "content": message}],
            persist_text=message,
            attachments=[],
            has_knowledge_context=True,
        )

    monkeypatch.setattr(chat_orchestrator, "build_chat_context", private_context)
    fake, _events = run_chat(monkeypatch, [content_stream("根据文档回答")])
    # 设计语言变化：空工具面现在不会作为空数组发给上游（vLLM 面 400 实证）——断言「搜索不在面」而非「key 存在」
    tool_names = {item["function"]["name"] for item in (fake.calls[0].get("tools") or [])}

    assert "search_web" not in tool_names


def test_rag_miss_keeps_web_fallback_and_drops_local_time_shortcut(monkeypatch, persisted):
    fake, _events = run_chat(monkeypatch, [content_stream("联网后备")])
    tool_names = {item["function"]["name"] for item in fake.calls[0]["tools"]}

    assert "search_web" in tool_names
    assert "get_current_time" not in tool_names


def test_single_tool(monkeypatch, persisted):
    script = [
        tool_call_stream((0, "call_1", "calculate", '{"expression": "1+1"}')),
        content_stream("等于2"),
    ]
    fake, events = run_chat(monkeypatch, script)

    assert [e["tool_name"] for e in events_of(events, "tool")] == ["calculate"]
    assert events_of(events, "done")[0]["tool_used"] == "calculate"
    # 第二次请求必须带回：assistant 的 tool_calls 声明 + 对应 tool 结果
    second_messages = fake.calls[1]["messages"]
    assert second_messages[-2]["role"] == "assistant"
    assert second_messages[-2]["tool_calls"][0]["id"] == "call_1"
    assert second_messages[-1] == {"role": "tool", "content": "2", "tool_call_id": "call_1"}


def test_workflow_tools_emit_ui_snapshot_events_not_lyrics(monkeypatch, persisted):
    """配置了 Workflow 后，编排器把 _ui_events 透传给前端、剥离后回填模型。"""
    snapshot = {"nodes": [{"id": "n1", "capability_id": "lyrics.generate"}], "edges": []}

    async def fake_execute(func_name, func_args):
        assert func_name == "add_workflow_chains"
        return ToolOutcome(
            result=json.dumps(
                {
                    "status": "configured",
                    "_ui_events": [{"type": "workflow_snapshot", "snapshot": snapshot}],
                },
                ensure_ascii=False,
            ),
            ok=True,
        )

    monkeypatch.setattr(chat_orchestrator, "execute_tool", fake_execute)
    fake, events = run_chat(
        monkeypatch,
        [
            tool_call_stream(
                (0, "call_cfg", "add_workflow_chains", '{"chains": [{"template_id": "lyrics_only", "slots": {"theme": "夏夜城市"}}]}')
            ),
            content_stream("已创建歌词节点"),
        ],
    )

    snapshots = [event for event in events if event["type"] == "workflow_snapshot"]
    assert snapshots and snapshots[0]["snapshot"] == snapshot
    # 回填模型的内容不得携带 _ui_events，也不含歌词正文外的 Draft
    tool_message = next(m for m in fake.calls[1]["messages"] if m["role"] == "tool")
    assert "_ui_events" not in tool_message["content"]
    assert json.loads(tool_message["content"])["status"] == "configured"


def test_multiple_tools_in_one_round(monkeypatch, persisted):
    script = [
        tool_call_stream(
            (0, "call_a", "calculate", '{"expression": "1+1"}'),
            (1, "call_b", "calculate", '{"expression": "2+2"}'),
        ),
        content_stream("2和4"),
    ]
    fake, events = run_chat(monkeypatch, script)

    assert [e["tool_name"] for e in events_of(events, "tool")] == ["calculate", "calculate"]
    tool_messages = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    # 按 index 聚合：两个调用的参数各归各，不会拼接串味
    assert {(m["tool_call_id"], m["content"]) for m in tool_messages} == {
        ("call_a", "2"),
        ("call_b", "4"),
    }


def test_tool_failure_does_not_break_stream(monkeypatch, persisted):
    script = [
        tool_call_stream((0, "call_1", "not_a_tool", "{}")),
        content_stream("工具不可用，直接回答"),
    ]
    fake, events = run_chat(monkeypatch, script)

    # 未知工具被拒绝，但流不断：结果单照样回填给模型继续对话
    assert events_of(events, "error") == []
    tool_message = next(m for m in fake.calls[1]["messages"] if m["role"] == "tool")
    assert "不支持的工具" in tool_message["content"]
    assert events_of(events, "done")[0]["history"][-1]["content"] == "工具不可用，直接回答"


def test_infinite_tool_requests_hit_round_limit(monkeypatch, persisted):
    rounds = chat_orchestrator.MAX_TOOL_ROUNDS
    script = [
        tool_call_stream((0, f"call_{i}", "calculate", '{"expression": "1+1"}')) for i in range(rounds)
    ] + [content_stream("最终回答")]
    fake, events = run_chat(monkeypatch, script)

    assert len(fake.calls) == rounds + 1
    final_call = fake.calls[-1]
    assert "tools" not in final_call  # 收尾轮不给工具：模型只能说话
    assert final_call["messages"][-1] == {
        "role": "system",
        "content": chat_orchestrator.TOOL_LIMIT_NOTICE,
    }
    assert events_of(events, "done")[0]["history"][-1]["content"] == "最终回答"


def test_per_round_tool_call_cap(monkeypatch, persisted):
    cap = chat_orchestrator.MAX_TOOL_CALLS_PER_ROUND
    calls = [(i, f"call_{i}", "calculate", '{"expression": "1+1"}') for i in range(cap + 2)]
    script = [tool_call_stream(*calls), content_stream("ok")]
    fake, events = run_chat(monkeypatch, script)

    assert len(events_of(events, "tool")) == cap  # 超额的没执行
    tool_messages = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == cap + 2  # 但每个 tool_call_id 都有应答（协议要求）
    skipped = [m for m in tool_messages if m["content"] == chat_orchestrator.TOOL_SKIPPED_RESULT]
    assert len(skipped) == 2


def test_same_error_short_circuits_after_two_failures(monkeypatch, persisted):
    """C13：同一工具以同一错误类别连续失败两次，后续轮次该工具从可用工具面摘除，
    且模型收到明确的停手 system note——不许死循环烧 token。"""

    async def always_timeout(func_name, func_args):
        return ToolOutcome(result="超时", ok=False, error_kind="timeout")

    # 中立化意图分类：不要让它把「hi」判成闲聊而收窄工具面，干扰本用例观察对象
    async def neutral_classify(*args, **kwargs):
        return {"intent": "unknown", "allow_tools": None}

    monkeypatch.setattr(chat_orchestrator, "classify_intent", neutral_classify)
    monkeypatch.setattr(chat_orchestrator, "execute_tool", always_timeout)
    fake, _events = run_chat(
        monkeypatch,
        [
            tool_call_stream((0, "c1", "calculate", '{"expression": "1+1"}')),
            tool_call_stream((0, "c2", "calculate", '{"expression": "2+2"}')),
            tool_call_stream((0, "c3", "calculate", '{"expression": "3+3"}')),
            content_stream("算了还是直接说吧"),
        ],
    )

    # 第一、二轮可用工具面含 calculate；第三轮起被摘除
    first_tools = {t["function"]["name"] for t in fake.calls[0]["tools"]}
    assert "calculate" in first_tools
    third_ones = fake.calls[2]
    third_tools = {t["function"]["name"] for t in third_ones.get("tools", [])}
    assert "calculate" not in third_tools
    # 停手指令已进入消息轨
    sys_notes = [m["content"] for m in fake.calls[2]["messages"] if m["role"] == "system"]
    assert chat_orchestrator.SAME_ERROR_SHORT_CIRCUIT_NOTICE in sys_notes


def test_distinct_error_kinds_do_not_short_circuit(monkeypatch, persisted):
    """C13：不同错误类别不算同错——模型换路径重试是合法行为，不该被误摘。"""
    kinds = iter(["timeout", "business_denied", "timeout"])

    async def mixed_fail(func_name, func_args):
        return ToolOutcome(result="失败", ok=False, error_kind=next(kinds))

    async def neutral_classify(*args, **kwargs):
        return {"intent": "unknown", "allow_tools": None}

    monkeypatch.setattr(chat_orchestrator, "classify_intent", neutral_classify)
    monkeypatch.setattr(chat_orchestrator, "execute_tool", mixed_fail)
    fake, _events = run_chat(
        monkeypatch,
        [
            tool_call_stream((0, "c1", "calculate", '{"expression": "1+1"}')),
            tool_call_stream((0, "c2", "calculate", '{"expression": "2+2"}')),
            tool_call_stream((0, "c3", "calculate", '{"expression": "3+3"}')),
            content_stream("done"),
        ],
    )
    third_tools = {t["function"]["name"] for t in fake.calls[2]["tools"]}
    assert "calculate" in third_tools  # 类别不同，不触短路


def test_textual_tool_call_is_converted_instead_of_shown(monkeypatch, persisted):
    async def fake_execute(func_name, func_args):
        assert func_name == "search_web"
        assert json.loads(func_args) == {"query": "最近人工智能行业新闻"}
        return ToolOutcome(result="搜索结果：AI 行业摘要", ok=True)

    monkeypatch.setattr(chat_orchestrator, "execute_tool", fake_execute)
    fake, events = run_chat(
        monkeypatch,
        [
            content_stream("search_web", "\n", '{"query": "最近人工智能行业新闻"}'),
            content_stream("根据搜索结果整理：AI 行业摘要"),
        ],
    )

    assert [event["tool_name"] for event in events_of(events, "tool")] == ["search_web"]
    content = "".join(event["content"] for event in events_of(events, "content"))
    assert "search_web" not in content
    assert content == "根据搜索结果整理：AI 行业摘要"
    assert len(fake.calls) == 2


def test_incomplete_textual_tool_prefix_remains_normal_content(monkeypatch, persisted):
    _fake, events = run_chat(monkeypatch, [content_stream("search_web 暂时不可用")])

    content = "".join(event["content"] for event in events_of(events, "content"))
    assert content == "联网搜索 暂时不可用"  # 工具名净化：内部函数名不上屏


def test_receipt_marks_rejected_tools_honestly():
    """空回复兜底回执必须讲真话：被拒的调用不能被写成'已完成'。"""
    from services.chat.chat_orchestrator import _compose_action_receipt

    outcomes = [
        {"name": "add_workflow_chains", "label": "搭建创作链", "ok": True, "summary": "已配置"},
        {"name": "update_chain_params", "label": "调整链参数", "ok": False, "summary": "链上没有该环节"},
    ]
    receipt = _compose_action_receipt(outcomes)
    assert "✗ 调整链参数" in receipt
    assert "链上没有该环节" in receipt
    assert "未能完成" in receipt


def test_receipt_empty_tools_falls_back():
    from services.chat.chat_orchestrator import _compose_action_receipt

    assert "换个说法" in _compose_action_receipt([])


def test_hollow_claim_detector():
    """创作请求里空谈动作=假，要纠错；解释性聊天空谈不算。"""
    from services.chat.chat_orchestrator import _claims_action_without_tools

    # 空谈：创作请求 + 排比谓词 + 零工具
    assert _claims_action_without_tools("写首歌", "Workflow 已排队执行，画布会显示运行状态", [])
    assert _claims_action_without_tools("生成一张图", "已配置图像节点", [])
    # 配了节点但没跑却声称已排队——空谈变体，要纠
    assert _claims_action_without_tools("写首歌", "已排队执行", ["add_workflow_chains"])
    # 纯解释/科普不算空谈
    assert not _claims_action_without_tools("画布是什么", "画布是编辑工作流的地方", [])
    # 空回复走另一条兜底路，不该被这里拦
    assert not _claims_action_without_tools("写首歌", "", [])


def test_chat_repo_interrupted_roundtrip(tmp_path, monkeypatch):
    """c14：中断标记随消息落库并正确读回，正常消息默认 False。"""
    import core.database as database
    from core.migrations import run_migrations
    from repositories import chat_repo

    monkeypatch.setattr(database, "DB_FILE", tmp_path / "t.db")
    run_migrations()
    with database.db_connection() as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, profile, created_at) "
            "VALUES (800, 'c14@local', 'x', '', '2026-01-01')"
        )

    chat_repo.append_message(800, "assistant", "半截回复", None, interrupted=True)
    chat_repo.append_message(800, "assistant", "完整回复", None)
    history = chat_repo.list_history(800, None)
    assert history[0]["interrupted"] is True
    assert "interrupted" not in history[1]


def test_hollow_claim_detector_partial_tools():
    """空谈变体：配了节点没跑，却说“已排队”——必须被纠错。"""
    from services.chat.chat_orchestrator import _claims_action_without_tools

    assert _claims_action_without_tools(
        "写歌词并出曲绘", "Workflow 已排队执行", ["add_workflow_chains"]
    )
    assert _claims_action_without_tools(
        "写歌词并出曲绘", "图像已生成在画布上", ["add_workflow_chains", "update_chain_params"]
    )
    # 实实在在跑过不算空谈
    assert not _claims_action_without_tools(
        "写歌词并出曲绘", "已排队执行", ["add_workflow_chains", "run_current_workflow"]
    )
    # 纯配置请求缺“跑”不算空谈（用户就没让跑）
    assert not _claims_action_without_tools(
        "先别跑，把歌词主题改成海边", "已修改参数", ["update_chain_params"]
    )


# ---------- C19：注入拦截落库 + Rule of Two ----------


def test_injection_block_is_persisted(monkeypatch, persisted):
    """被拦截的对话必须留会话痕迹：用户消息 + 拦截回复都落库（C19-④）。"""
    monkeypatch.setattr(
        "core.injection_guard.verdict",
        lambda message="", rag_text="", reply="": {"action": "block", "reasons": ["jailbreak_pattern"], "severity": "high"},
    )
    fake, events = run_chat(monkeypatch, [])  # 拦截路径不应碰 LLM，空剧本能跑完即证明
    assert fake.calls == []
    roles = [r for r, _ in persisted]
    assert roles == ["user", "assistant"]
    assert "不合适的指令模式" in persisted[1][1]
    assert events_of(events, "done")[0]["history"][-1]["role"] == "assistant"


def test_rule_of_two_blocks_state_change_after_search(monkeypatch, persisted):
    """search_web 结果进上下文后，同轮的状态变更工具必须被冻结且原因回填模型。"""
    executed = []

    async def fake_execute(func_name, func_args):
        executed.append(func_name)
        return ToolOutcome(result="搜到的网页内容", ok=True)

    monkeypatch.setattr(chat_orchestrator, "execute_tool", fake_execute)
    script = [
        tool_call_stream((0, "call_s", "search_web", '{"query": "热点"}')),
        tool_call_stream((0, "call_c", "run_current_workflow", "{}")),
        content_stream("好的"),
    ]
    fake, _events = run_chat(monkeypatch, script)

    # 只有 search_web 真执行了；run_current_workflow 被策略拦下
    assert executed == ["search_web"]
    # 搜索结果带上了不可信围栏再回填模型（messages 列表被各轮原地复用，按 id 定位）
    final_messages = fake.calls[-1]["messages"]
    by_id = {m.get("tool_call_id"): m["content"] for m in final_messages if m.get("role") == "tool"}
    assert "<<<不可信内容开始（网络搜索结果）>>>" in by_id["call_s"]
    assert "搜到的网页内容" in by_id["call_s"]
    # 状态变更工具的 tool 结果是拒绝文案
    assert "安全策略拒绝" in by_id["call_c"]


def test_state_change_without_search_still_works(monkeypatch, persisted):
    """没有不可信输入时状态变更工具照常执行——Rule of Two 不能误伤正常链路。"""
    executed = []

    async def fake_execute(func_name, func_args):
        executed.append(func_name)
        return ToolOutcome(result='{"status": "queued"}', ok=True)

    monkeypatch.setattr(chat_orchestrator, "execute_tool", fake_execute)
    script = [
        tool_call_stream((0, "call_c", "run_current_workflow", "{}")),
        content_stream("已提交"),
    ]
    _fake, _events = run_chat(monkeypatch, script)
    assert executed == ["run_current_workflow"]


# ---------- 工具名净化器（2026-10-11 真机泄漏事故防线） ----------


def test_tool_name_in_reply_scrubbed_and_never_leaks(monkeypatch, persisted):
    """模型把内部函数名说给用户听时：流上屏、done.history、落库三处都必须干净。"""
    _fake, events = run_chat(
        monkeypatch,
        [content_stream("已搭好。接下来可以调用 ", "run_current_workflow", " 执行。")],
    )

    text = "".join(e["content"] for e in events_of(events, "content"))
    assert "run_current_workflow" not in text
    assert "运行工作流" in text
    done = events_of(events, "done")[0]
    assert "run_current_workflow" not in done["history"][-1]["content"]
    assert persisted[-1] == ("assistant", text)


def test_tool_name_split_across_deltas_also_scrubbed(monkeypatch, persisted):
    """跨分片拼出的工具名同样拦得下：净化器压尾巴，凑齐才替换。"""
    _fake, events = run_chat(
        monkeypatch,
        [content_stream("接下来点 ", "run_cur", "rent_workflow", " 就能跑")],
    )
    text = "".join(e["content"] for e in events_of(events, "content"))
    assert "run_cur" not in text  # 连半截名字都不许上屏
    assert text == "接下来点 运行工作流 就能跑"


def test_normal_reply_untouched_by_scrubber(monkeypatch, persisted):
    """普通文本逐字通过，净化器不误伤、不吃尾巴。"""
    _fake, events = run_chat(monkeypatch, [content_stream("歌词", "已生成", "，请看画布")])
    text = "".join(e["content"] for e in events_of(events, "content"))
    assert text == "歌词已生成，请看画布"


def test_followups_drop_suggestions_leaking_tool_names(monkeypatch, persisted):
    """追问建议若由脏文本派生又含工具名，整条丢弃（源头净化 + 兜网过滤双保险）。"""
    import services.chat.chat_orchestrator as orch

    monkeypatch.setattr("core.config.FOLLOWUP_SUGGESTIONS_ENABLED", True)

    class _C:
        def complete(self, **kw):
            from services.llm import ChatReply

            return ChatReply(
                content='["如何调用 run_current_workflow？", "封面要不要换个比例？"]'
            )

    monkeypatch.setattr(orch, "client", _C())
    result = asyncio.run(orch._generate_followups("歌词→封面图", "已搭好，点运行即可"))
    assert result == ["封面要不要换个比例？"]


# ---------- 模型野格式工具调用回收（Kunbot 网关切槽实证：DSML / <tool_call> 杂行） ----------


def test_dsml_invoke_is_converted_to_tool_call():
    dsml = '<｜DSML｜invoke name="search_web"><｜DSML｜parameter name="query" string="true">智能体含义</｜DSML｜parameter></｜DSML｜invoke>'
    assert chat_orchestrator.parse_textual_tool_call(dsml) == ("search_web", '{"query": "智能体含义"}')


def test_tool_call_wrapper_bare_name_json():
    out = chat_orchestrator.parse_textual_tool_call('<tool_call>search_web {"a": 1}</tool_call>')
    assert out == ("search_web", '{"a": 1}')


def test_hybrid_trailing_empty_object_is_tolerated():
    out = chat_orchestrator.parse_textual_tool_call('search_web {"query": "京剧"}{}')
    assert out == ("search_web", '{"query": "京剧"}')


def test_plain_text_with_tool_mention_not_swallowed():
    assert chat_orchestrator.parse_textual_tool_call("你可以用 search_web 查") is None


def test_dsml_prefix_marker_captured_as_candidate_prefix():
    assert chat_orchestrator._could_be_textual_tool_call_prefix("<｜DSML")
    assert chat_orchestrator._could_be_textual_tool_call_prefix("<too")
    assert not chat_orchestrator._could_be_textual_tool_call_prefix("普通中文一句")


def test_scrubber_strips_protocol_junk_fragments():
    scr = chat_orchestrator._ToolNameScrubber()
    out = scr.feed("答案</tool_call>来了") + scr.feed("，还有</arg_value>尾巴")
    out += scr.flush()
    assert "</tool_call>" not in out and "</arg_value>" not in out
    assert "答案" in out and "尾巴" in out


# ---------- K27R：推理模型的思考期心跳（不让 60s 空闲看门狗误杀活流） ----------


def test_reasoning_deltas_emit_throttled_heartbeats_not_content(monkeypatch, persisted):
    """上游是推理模型时内容面什么也不出，但 SSE 必须有心跳把活性信号传给前端。"""
    deltas = [
        chat_orchestrator.StreamDelta(reasoning=True),
        chat_orchestrator.StreamDelta(reasoning=True),
        chat_orchestrator.StreamDelta(content="你好"),
        chat_orchestrator.StreamDelta(content="世界"),
    ]
    _fake, events = run_chat(monkeypatch, [deltas])
    event_types = [e["type"] for e in events]
    assert "heartbeat" in event_types
    # 至少有两条心跳：承建开锁（C27）+ 思考增量节流各一条
    assert event_types.count("heartbeat") >= 2
    # 正文只有正文，没有 reasoning 垃圾
    payload = "".join(e["content"] for e in events_of(events, "content"))
    assert "reasoning" not in payload and payload.endswith("你好世界")
