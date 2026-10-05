"""对话编排器（C2 拆分件之三）：串起 备菜 → 多轮工具循环 → 流式输出 → 持久化。

C3 多轮工具循环（ReAct：推理 → 行动 → 观察 → 再推理）：
- 模型一次返回多个 tool_calls 时全部执行（按 index 聚合流式分片）；
- 工具结果回填后模型仍要工具就进入下一轮，最多 MAX_TOOL_ROUNDS 轮；
- 每轮最多执行 MAX_TOOL_CALLS_PER_ROUND 个调用，超出的必须逐个回复占位
  tool 消息（协议要求每个 tool_call_id 都有应答，否则下一轮请求非法）；
- 轮数耗尽后追加一条说明并发起"无工具"的收尾请求，强制模型给出最终回答，
  杜绝模型无限请求工具把对话拖死。

SSE 事件协议不变：content / tool / done / error，前端零改动。
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import HTTPException
from starlette.concurrency import iterate_in_threadpool

from agents.tools.registry import tools_map, tools_schema
from core import config
from core.injection_guard import (
    RULE_OF_TWO_REFUSAL,
    STATE_CHANGING_TOOLS,
    UNTRUSTED_INGRESS_TOOLS,
    guard_untrusted_content,
    scan_assistant_reply,
)
from repositories.chat_repo import append_message, pop_last_turn, truncate_from_message
from services.capabilities import CapabilityContext, use_capability_context
from services.chat.context_builder import build_chat_context
from services.chat.intent_classifier import classify as classify_intent
from services.chat.tool_executor import ToolOutcome, execute_tool
from services.llm import StreamDelta, ToolCallDelta, get_chat_client
from services.workflow_service import reset_canvas_read_stamp

# C12：不再直接碰 Provider SDK。client 走抽象工厂（默认 zhipu，LLM_PROVIDER 可换），
# 模块级赋值保住测试 mock 面（monkeypatch chat_orchestrator.client）。
client: Any = get_chat_client()

# 模型名统一走环境变量（config.CHAT_MODEL），不在代码里写死
CHAT_MODEL = config.CHAT_MODEL

MAX_TOOL_ROUNDS = 5
MAX_TOOL_CALLS_PER_ROUND = 5

TOOL_LIMIT_NOTICE = (
    "已达到本次对话的工具调用轮数上限，请基于已获得的信息直接给出最终回答，不要再请求任何工具。"
)
TOOL_SKIPPED_RESULT = "已超出单轮工具调用数量上限，本次调用未执行。请基于已有结果回答。"
HOLLOW_CLAIM_NOTICE = (
    "你刚才声称已经执行了画布/工作流操作，但实际上一个工具都没有调用。"
    "要么立刻调用相应 Workflow 工具真正执行，要么明确告诉用户你无法执行，绝不许空谈。"
)
SAME_ERROR_SHORT_CIRCUIT_NOTICE = (
    "同一个工具以同样的原因连续失败了两次。不要再用相同调用重试，"
    "换一条路径或如实告诉用户该操作当前不可用，并给出可以替代的选项。"
)

# 声明动作的谓词（模型空谈时喜欢说的话） + 创作请求的动词（过滤纯解释性回答）
_ACTION_CLAIM_HINTS = ("已排队", "已开始", "已执行", "正在执行", "已配置", "已生成", "已连接到", "开始跑")
_CREATIVE_VERBS = ("写", "生成", "做", "画", "创作", "出图", "出一张")


def _claims_action_without_tools(message: str, reply: str, tools_used: list[str]) -> bool:
    """模型声称动手了但轮不到工具：两种毛都抓。

    1. 完全零工具 + 创作类请求 + 回答里有动作谓词；
    2. 配了节点但没跑（声称"已排队"却没调 run_current_workflow）：
       实测过的变体——配好了说人话，就是不点成了运行。
    """
    if not reply.strip():
        return False
    if not any(verb in message for verb in _CREATIVE_VERBS):
        return False
    if not tools_used:
        return any(hint in reply for hint in _ACTION_CLAIM_HINTS)
    if "run_current_workflow" in tools_used:
        return False
    # 配了但没跑：只要出现执行语谓词就算空谈
    return any(hint in reply for hint in _ACTION_CLAIM_HINTS)
WEB_SEARCH_TOOL_NAME = "search_web"
TEXT_TOOL_CALL_MAX_LENGTH = 1200

logger = logging.getLogger("assistant")


def parse_textual_tool_call(text: str) -> tuple[str, str] | None:
    """识别模型偶尔输出的旧式文本工具调用，不把它直接展示给用户。

    智谱的标准响应是 ``delta.tool_calls``，但某些模型/兼容层会把调用写成
    ``search_web {"query": "..."}``。这不是正常回答，必须转换回编排器的
    结构化路径；解析失败时返回 None，普通文本仍按原样流出。
    """
    candidate = text.strip()
    if not candidate or len(candidate) > TEXT_TOOL_CALL_MAX_LENGTH:
        return None

    for tool_name in sorted(tools_map, key=len, reverse=True):
        if not candidate.startswith(tool_name):
            continue
        rest = candidate[len(tool_name) :].lstrip()
        if not rest.startswith("{"):
            continue
        try:
            arguments, end = json.JSONDecoder().raw_decode(rest)
        except json.JSONDecodeError:
            continue
        trailing = rest[end:].strip().removeprefix("```").strip()
        if trailing or not isinstance(arguments, dict):
            continue
        return tool_name, json.dumps(arguments, ensure_ascii=False)
    return None


def _could_be_textual_tool_call_prefix(text: str) -> bool:
    """判断尚未完整到达的文本是否仍可能是工具调用。"""
    candidate = text.lstrip()
    if not candidate or len(candidate) > TEXT_TOOL_CALL_MAX_LENGTH:
        return False
    return any(tool_name.startswith(candidate) or candidate.startswith(tool_name) for tool_name in tools_map)


def _tools_for_context(has_knowledge_context: bool) -> list[dict]:
    """有私有资料时锁住联网工具；无命中时只保留计算和联网后备。

    ``generate_lyrics_block`` 只对 Workflow Runner 内部使用——Assistant 必须通过
    configure_lyrics_workflow/run_current_workflow 操作画布，不能绕过 Workflow
    直接生成一首歌词丢回聊天框。
    """
    direct_capability_tools = {"generate_lyrics_block"}
    if has_knowledge_context:
        return [
            tool
            for tool in tools_schema
            if tool.get("function", {}).get("name") not in {WEB_SEARCH_TOOL_NAME}
            | direct_capability_tools
        ]
    return [
        tool
        for tool in tools_schema
        if tool.get("function", {}).get("name") not in {"get_current_time"}
        | direct_capability_tools
    ]


def split_ui_events(result_text: str) -> tuple[str, list[dict]]:
    """把工具结果里的 ``_ui_events`` 剥出来转成界面事件，模型只看到业务摘要。"""
    try:
        payload = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return result_text, []
    if not isinstance(payload, dict) or not isinstance(payload.get("_ui_events"), list):
        return result_text, []
    events = [event for event in payload.pop("_ui_events") if isinstance(event, dict)]
    return json.dumps(payload, ensure_ascii=False), events


def merge_tool_call_delta(pending: dict[int, dict], delta_calls: list[ToolCallDelta]) -> None:
    """把流式分片按 index 聚合成完整的 tool_calls。

    流式模式下一个工具调用会被拆成多个分片（id/name 先到，arguments 逐段追加），
    多个并行调用靠分片上的 index 区分。入参已是抽象层归一化后的中立类型，
    不再认识任何 Provider SDK 的 chunk 形状。
    """
    for tool_call in delta_calls:
        slot = pending.setdefault(tool_call.index, {"id": "", "name": "", "arguments": ""})
        if tool_call.id:
            slot["id"] = tool_call.id
        if tool_call.name:
            slot["name"] = tool_call.name
        if tool_call.arguments:
            slot["arguments"] += tool_call.arguments


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


_TOOL_DISPLAY_NAMES = {
    "configure_lyrics_workflow": "配置歌词节点",
    "configure_image_workflow": "配置图像节点",
    "run_current_workflow": "运行工作流",
    "clear_workflow_draft": "清空画布",
    "read_workflow_state": "读取画布",
    "get_workflow_run_status": "查询运行状态",
    "search_web": "联网搜索",
    "calculate": "计算",
    "get_current_time": "查询时间",
    "generate_lyrics_block": "生成歌词",
}


def _summarize_tool_outcome(tool_name: str, outcome, model_result: str) -> dict:
    """把一张 ToolOutcome 缩成回执可用的一行：中文动作名 + 成败 + 原因。"""
    summary = model_result
    try:
        payload = json.loads(model_result)
        if isinstance(payload, dict):
            summary = payload.get("message") or payload.get("error") or payload.get("status") or model_result
    except (json.JSONDecodeError, TypeError):
        pass
    return {
        "name": tool_name,
        "label": _TOOL_DISPLAY_NAMES.get(tool_name, tool_name),
        "ok": outcome.ok,
        "summary": str(summary)[:160],
    }


def _compose_action_receipt(tool_outcomes: list[dict]) -> str:
    """模型没说话时的兜底第十三条：逐条列出本轮动作的真实成败，不抵赖。"""
    if not tool_outcomes:
        return "这次没有得到可输出的内容，请换个说法再试一次。"
    lines: list[str] = []
    for item in tool_outcomes:
        mark = "✓" if item["ok"] else "✗"
        lines.append(f"{mark} {item['label']}：{item['summary']}")
    failed = [item for item in tool_outcomes if not item["ok"]]
    if failed:
        tail = "部分内容未能完成，失败原因已按条列出；可以让我重试或调整需求。"
    else:
        tail = "生成结果展示在工作区画布的节点上，可随时追问进度。"
    return "本轮操作回执：\n" + "\n".join(lines) + "\n" + tail


async def stream_chat(
    user: dict,
    message: str,
    project_id: int | None = None,
    attachments: list[dict] | None = None,
    regenerate: bool = False,
    edit_from_id: int | None = None,
) -> AsyncGenerator[str, None]:
    """对话流外层：兼做流中断兜底。

    SSE 响应头发出后全局异常 handler 就管不到了（无法再发 500），生成器里的
    异常只会表现为前端断流。这里统一兼做：记结构化错误日志 + 向前端发一条
    error 事件体面收尾（前端对未知类型静默忽略，兼容旧版本）。
    """
    started = time.perf_counter()
    try:
        async for event in _orchestrate(
            user, message, project_id, attachments, regenerate, edit_from_id
        ):
            yield event
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "对话流中断: %s",
            type(exc).__name__,
            extra={
                "evt": "chat_stream_error",
                "error_type": type(exc).__name__,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        yield _sse({"type": "error", "message": "服务器开小差了，请稍后重试"})


async def _orchestrate(
    user: dict,
    message: str,
    project_id: int | None = None,
    attachments: list[dict] | None = None,
    regenerate: bool = False,
    edit_from_id: int | None = None,
) -> AsyncGenerator[str, None]:
    if client is None:
        raise HTTPException(status_code=503, detail="未配置 API_KEY，聊天功能暂不可用")

    if regenerate and edit_from_id is not None:
        raise HTTPException(status_code=400, detail="regenerate 与 edit_from_id 互斥，只能传一个")

    # 重生成模式（UI1）：先在事务语义下截掉数据库尾轮（user+assistant），
    # 之后 build_chat_context 重建的上下文就不含旧答案，本次 message 当作新问重走全流程。
    # 尾轮混有 system-notice 等事件时弹回 409——上下文已漂移，逼用户显式重问。
    if regenerate:
        truncated = await asyncio.to_thread(pop_last_turn, user["id"], project_id)
        if not truncated:
            raise HTTPException(
                status_code=409,
                detail="最新一轮对话之后发生了其他事件，无法重新生成，请直接发送新消息",
            )

    # 编辑重发（UI2-②）：从指定 user 消息起连同后续所有消息一并截断，本次 message
    # 当作改写后的新问题重新落库。删除范围包含后续 user 消息，前端已在确认框里
    # 向用户明示丢失数量；编辑操作不可撤销，不做软删。
    if edit_from_id is not None:
        truncated = await asyncio.to_thread(
            truncate_from_message, user["id"], project_id, edit_from_id
        )
        if not truncated:
            raise HTTPException(
                status_code=409,
                detail="要编辑的消息不存在或已变化，请刷新后重试",
            )

    chat_started = time.perf_counter()
    # 每轮对话独立重置“画布已读”印记：工具必须先读再改，跨轮不传递
    reset_canvas_read_stamp()
    ctx = await build_chat_context(user["id"], message, project_id, attachments)
    messages = ctx.messages

    # C16: 三级意图漏斗——规则秒判 → 便宜模型结构化分类 → 兜底
    intent_result = await classify_intent(
        message,
        has_knowledge_context=ctx.has_knowledge_context,
        enable_classifier=config.INTENT_CLASSIFIER_ENABLED,
    )
    intent_type = intent_result.get("intent", "unknown")
    allowed_by_intent = intent_result.get("allow_tools", set())
    available_tools = _tools_for_context(ctx.has_knowledge_context)
    if allowed_by_intent:
        # 意图白名单：收窄为和场景相关的子集
        available_tools = [
            t for t in available_tools if t.get("function", {}).get("name") in allowed_by_intent
        ]
        logger.info(
            "意图收窄工具面",
            extra={"evt": "intent_narrowed", "intent": intent_type, "allowed": sorted(allowed_by_intent)},
        )
    elif intent_type == "chat":
        # 闲聊：断开工生产能力（骗局不重复言说）
        available_tools = [
            t for t in available_tools if t.get("function", {}).get("name") == "search_web"
        ]

    reply = ""
    tools_used: list[str] = []
    # 真实回执需要“谁成谁败”：记录每次工具调用的结果单摘要
    tool_outcomes: list[dict] = []
    # C13 同错短路：同一 (工具, 错误类别) 失败计数；达到阈值后从本轮后续
    # 可用工具中摘除该工具，并向模型发明确的停手指令——修复死循环重试
    tool_failure_counts: dict[tuple[str, str], int] = {}
    short_circuited_tools: set[str] = set()
    tool_rounds = 0
    hollow_claim_corrected = False
    force_tool_choice_once = False
    # Rule of Two（C19-②）：本轮一旦引入过不可信网络内容，状态变更类工具即被冻结
    untrusted_ingress_seen = False
    llm_started = time.perf_counter()
    first_token_at: float | None = None
    stream: Any = None  # 上游 SSE 流对象（智谱 SDK Stream），中断时要 close 停止计费

    try:  # 中断收尸：客户端断开/任务取消时，半截回复要落库+打标，上游流要关（C14）
        # 防注入屏障（请求开始路）：挡住了就看似正常性地拒绝，不走 LLM 调用
        from core.injection_guard import verdict as injection_verdict

        guard = injection_verdict(message=message)
        if guard.get("action") == "block":
            logger.warning(
                "疑似注入式攻击已拦截",
                extra={
                    "evt": "injection_blocked",
                    "reasons": guard.get("reasons"),
                    "severity": guard.get("severity"),
                },
            )
            blocked_message = (
                "我注意到这条消息包含了不合适的指令模式。"
                "请你说明白你到底想要什么，我可以帮你正常做。"
            )
            # C19-④：拦截也要落库——审计需要会话痕迹，用户刷新后也不该“平空消失一轮”
            await asyncio.to_thread(
                append_message, user["id"], "user", ctx.persist_text, project_id, ctx.attachments
            )
            await asyncio.to_thread(
                append_message, user["id"], "assistant", blocked_message, project_id
            )
            blocked_history = [*ctx.clean_history, {"role": "assistant", "content": blocked_message}]
            yield _sse({"type": "content", "content": blocked_message})
            yield _sse({"type": "done", "history": blocked_history, "tool_used": None, "citations": []})
            return

        # ReAct 主循环：前 MAX_TOOL_ROUNDS 轮允许工具；最后一轮强制"只许说话"收尾
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            allow_tools = round_no < MAX_TOOL_ROUNDS
            if not allow_tools:
                # 走到这里说明前面每一轮模型都在要工具：明示上限，逼出最终回答
                messages.append({"role": "system", "content": TOOL_LIMIT_NOTICE})

            # C12：抽象层接口——归一化 StreamDelta 流，不再碰 SDK chunk 形状；
            # 返回的句柄可 close（中断时停上游计费）。空谈纠错轮强制 tool_choice=required。
            # kwargs 里不放 None 值的键：收尾轮「不给工具」在请求记录里必须体现为
            # 无 tools 键——既与旧行为一致，也让 "tools" not in call 的测试断言保持真实。
            stream_kwargs: dict = {"model": CHAT_MODEL, "messages": messages}
            if allow_tools:
                stream_kwargs["tools"] = available_tools
                if force_tool_choice_once:
                    stream_kwargs["tool_choice"] = "required"
                    force_tool_choice_once = False
            stream = await asyncio.to_thread(client.stream, **stream_kwargs)

            pending_tool_calls: dict[int, dict] = {}
            textual_tool_candidate = ""
            # 兼容少数模型/代理把 function calling 错误降级成普通文本的情况。
            # 只暂存“像工具名开头”的前缀，普通回答仍逐 chunk 流出。
            async for delta in iterate_in_threadpool(stream):
                if not isinstance(delta, StreamDelta):
                    continue
                if delta.tool_calls:
                    if textual_tool_candidate:
                        fallback_call = parse_textual_tool_call(textual_tool_candidate)
                        if not fallback_call:
                            reply += textual_tool_candidate
                            yield _sse({"type": "content", "content": textual_tool_candidate})
                        textual_tool_candidate = ""
                    merge_tool_call_delta(pending_tool_calls, delta.tool_calls)
                    continue
                content = delta.content
                if content:
                    if textual_tool_candidate:
                        textual_tool_candidate += content
                    elif _could_be_textual_tool_call_prefix(content):
                        textual_tool_candidate = content
                    else:
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                        reply += content
                        yield _sse({"type": "content", "content": content})

            if textual_tool_candidate:
                fallback_call = parse_textual_tool_call(textual_tool_candidate)
                if fallback_call and not pending_tool_calls:
                    tool_name, arguments = fallback_call
                    pending_tool_calls[0] = {
                        "id": f"text-call-{round_no}",
                        "name": tool_name,
                        "arguments": arguments,
                    }
                else:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    reply += textual_tool_candidate
                    yield _sse({"type": "content", "content": textual_tool_candidate})

            if not pending_tool_calls:
                # 空谈检测：模型凭空声称“已排队/已配置”却一个工具都没调——
                # 给一次纠错机会（只补一轮），逼它要么真调用要么如实说做不到。
                if not hollow_claim_corrected and _claims_action_without_tools(message, reply, tools_used):
                    hollow_claim_corrected = True
                    force_tool_choice_once = True
                    logger.warning(
                        "检测到空谈式回复，自动触发纠错轮",
                        extra={"evt": "hollow_claim", "reply_preview": reply[:120]},
                    )
                    messages.append({"role": "user", "content": message})
                    messages.append({"role": "assistant", "content": reply})
                    messages.append({"role": "system", "content": HOLLOW_CLAIM_NOTICE})
                    reply = ""
                    continue
                break  # 模型不再要工具：本轮输出即最终回答

            tool_rounds += 1
            tool_calls = [pending_tool_calls[i] for i in sorted(pending_tool_calls)]
            messages.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {"name": call["name"], "arguments": call["arguments"]},
                        }
                        for call in tool_calls
                    ],
                }
            )
            for position, call in enumerate(tool_calls):
                if position >= MAX_TOOL_CALLS_PER_ROUND:
                    # 超额调用不执行，但必须应答该 tool_call_id，否则下一轮请求非法
                    messages.append({"role": "tool", "content": TOOL_SKIPPED_RESULT, "tool_call_id": call["id"]})
                    continue
                if untrusted_ingress_seen and call["name"] in STATE_CHANGING_TOOLS:
                    # Rule of Two：搜索结果（可能埋指令）已入上下文，状态变更不执行，
                    # 把拒绝原因回填给模型让它如实转告用户
                    logger.warning(
                        "Rule of Two 拦截状态变更工具",
                        extra={"evt": "rule_of_two_blocked", "tool": call["name"]},
                    )
                    refusal_outcome = ToolOutcome(result=RULE_OF_TWO_REFUSAL, ok=False)
                    tool_outcomes.append(
                        _summarize_tool_outcome(call["name"], refusal_outcome, RULE_OF_TWO_REFUSAL)
                    )
                    messages.append(
                        {"role": "tool", "content": RULE_OF_TWO_REFUSAL, "tool_call_id": call["id"]}
                    )
                    continue
                yield _sse({"type": "tool", "tool_name": call["name"]})
                # 跑腿的绝不抛异常：成功/被拒/失败都是一张结果单，这里不分叉
                with use_capability_context(
                    CapabilityContext(
                        user_id=user["id"],
                        project_id=project_id,
                        source="assistant",
                        user_message=message,
                    )
                ):
                    outcome = await execute_tool(call["name"], call["arguments"])
                # 工具可以携带界面事件（如 Workflow Draft 快照），立即透传给前端；
                # 剥离后再回填模型，避免模型把 Draft 当成歌词内容。
                model_result, ui_events = split_ui_events(outcome.result)
                if call["name"] in UNTRUSTED_INGRESS_TOOLS:
                    # C19-②：网络内容回填模型前 scrub+围栏，并触发 Rule of Two 冻结
                    untrusted_ingress_seen = True
                    model_result = guard_untrusted_content(model_result, "网络搜索结果")
                for ui_event in ui_events:
                    yield _sse(ui_event)
                tools_used.append(call["name"])
                tool_outcomes.append(_summarize_tool_outcome(call["name"], outcome, model_result))
                messages.append({"role": "tool", "content": model_result, "tool_call_id": call["id"]})
                # C13：同类失败 ≥2 次后同错短路——让模型无法在本轮继续重试同一调用
                if not outcome.ok and outcome.error_kind:
                    key = (call["name"], outcome.error_kind)
                    tool_failure_counts[key] = tool_failure_counts.get(key, 0) + 1
                    if tool_failure_counts[key] >= 2 and call["name"] not in short_circuited_tools:
                        short_circuited_tools.add(call["name"])
                        available_tools = [
                            t
                            for t in available_tools
                            if t.get("function", {}).get("name") != call["name"]
                        ]
                        messages.append({"role": "system", "content": SAME_ERROR_SHORT_CIRCUIT_NOTICE})
                        logger.warning(
                            "同类工具失败达到阈值，本轮摘除该工具",
                            extra={
                                "evt": "tool_short_circuit",
                                "tool": call["name"],
                                "error_kind": outcome.error_kind,
                            },
                        )

        tool_used = tools_used[-1] if tools_used else None

        # 空气泡根治：模型调完工具后可能最终一个字也不说（实测线上历史出现
        # content="" 的 assistant 消息，前端显示成永久空白气泡）。
        # 为空时合成“诚实的回执”：谁成了、谁败了、为什么，绝不能把被拒的调用
        # 伪装成已执行（用户实测：回执自称调用了图像配置，画布上根本没有节点）。
        if not reply.strip():
            reply = _compose_action_receipt(tool_outcomes)
            yield _sse({"type": "content", "content": reply})

        # C19-① 输出层接线：流式内容已逐 token 发出，无法事后撤回，
        # 这里做终态扫描留审计痕迹（阯断式输出防护需要改缓冲架构，归停车场）
        leak = scan_assistant_reply(reply)
        if leak.get("leaked"):
            logger.warning(
                "回复疑似携带内部提示词内容",
                extra={"evt": "prompt_leak_suspected", "markers": leak["markers"]},
            )
    except (GeneratorExit, asyncio.CancelledError):
        # GeneratorExit/CancelledError 不走 except Exception 通道，必须单独拦：
        # 关上游流（停止继续计费）+ 落库半截回复（用户重进还能看到）
        logger.warning(
            "对话流被中断，保存半截回复",
            extra={
                "evt": "chat_stream_interrupted",
                "reply_chars": len(reply),
                "tools_used": tools_used,
                "duration_ms": round((time.perf_counter() - chat_started) * 1000, 1),
            },
        )
        if stream is not None:
            close = getattr(stream, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(close)
        await asyncio.to_thread(
            append_message, user["id"], "user", ctx.persist_text, project_id, ctx.attachments
        )
        await asyncio.to_thread(
            append_message,
            user["id"],
            "assistant",
            reply.strip() or "（本轮回复尚未产出内容就被中断）",
            project_id,
            None,
            None,
            True,
        )
        raise

    clean_history = ctx.clean_history
    # append_message 返回行 id：回填进 done 事件的历史项，本轮新消息立刻带上 id，
    # 前端不用为「消息可操作（评分/编辑）」多做一次历史重拉（UI2）
    user_row_id = await asyncio.to_thread(
        append_message,
        user["id"],
        "user",
        ctx.persist_text,
        project_id,
        ctx.attachments,
    )
    if clean_history and clean_history[-1].get("role") == "user" and user_row_id:
        clean_history[-1]["id"] = user_row_id
    assistant_history_item: dict = {"role": "assistant", "content": reply}
    if ctx.citations:
        assistant_history_item["citations"] = ctx.citations
    clean_history.append(assistant_history_item)

    if ctx.citations:
        assistant_row_id = await asyncio.to_thread(
            append_message,
            user["id"],
            "assistant",
            reply,
            project_id,
            None,
            ctx.citations,
        )
    else:
        assistant_row_id = await asyncio.to_thread(
            append_message, user["id"], "assistant", reply, project_id
        )
    if assistant_row_id:
        assistant_history_item["id"] = assistant_row_id

    now = time.perf_counter()
    logger.info(
        "对话完成",
        extra={
            "evt": "chat_done",
            "model": CHAT_MODEL,
            "tool": tool_used,
            "tools_used": tools_used,
            "tool_rounds": tool_rounds,
            "history_items": len(clean_history),
            "reply_chars": len(reply),
            # TTFT 从首次请求 LLM 算起；若走了工具分支，包含工具耗时（用户体感口径）
            "ttft_ms": round(((first_token_at or now) - llm_started) * 1000, 1),
            "llm_ms": round((now - llm_started) * 1000, 1),
            "total_ms": round((now - chat_started) * 1000, 1),
        },
    )
    yield _sse(
        {
            "type": "done",
            "history": clean_history,
            "tool_used": tool_used,
            "citations": ctx.citations,
        }
    )

    # UI2-③：done 之后再出追问建议（open-webui FollowUps 设计）——不阻塞正文上屏，
    # 失败静默降级为空列表，绝不让一次锦上添花变成用户可见的错误
    followups = await _generate_followups(message, reply)
    if followups:
        yield _sse({"type": "suggestions", "items": followups})


_FOLLOWUP_SYSTEM = (
    "你是一个对话引导助手。用户刚和 AI 创作工作站进行了一轮问答。"
    "请基于这轮问答，生成 2~3 条用户接下来可能想问的问题。"
    "要求：每条不超过 20 个汉字；必须是用户向 AI 提问的第一人称视角；"
    "与这轮内容直接相关、具体可执行；不要重复这轮已经回答的内容。"
    "严格只输出一个 JSON 数组，例如 [\"问题一\", \"问题二\"]，不要输出任何其他文字。"
)


async def _generate_followups(user_message: str, reply: str) -> list[str]:
    """生成追问建议；任何环节出错都返回 []（降级语义：没有建议=没有功能失效）。"""
    if not config.FOLLOWUP_SUGGESTIONS_ENABLED or client is None:
        return []
    if not reply.strip():
        return []
    try:
        followup_reply = await asyncio.to_thread(
            client.complete,
            model=config.CHAT_MODEL,
            messages=[
                {"role": "system", "content": _FOLLOWUP_SYSTEM},
                {
                    "role": "user",
                    "content": f"用户问：{user_message[:500]}\n\nAI 答：{reply[:1000]}",
                },
            ],
            max_tokens=150,
        )
        raw = (followup_reply.content or "").strip()
        # 模型偶尔用 ```json 围栏包裹，剥掉再解析；找不到数组就放弃
        if "```" in raw:
            raw = raw.split("```")[1] if raw.count("```") >= 2 else raw.strip("`")
            raw = raw.lstrip("json").strip()
        start, end = raw.find("["), raw.rfind("]")
        if start < 0 or end <= start:
            return []
        items = json.loads(raw[start : end + 1])
        if not isinstance(items, list):
            return []
        cleaned = [str(item).strip() for item in items if str(item).strip()]
        return [item[:40] for item in cleaned[:3]]
    except Exception:
        logger.info("追问建议生成失败，本轮略过", extra={"evt": "followup_fallback"})
        return []
