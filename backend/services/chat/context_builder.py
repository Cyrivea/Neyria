"""上下文构建器：组装模型消息，并保留 RAG 来源引用元数据。"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from agents.neyria import build_system_prompt
from core import config
from core.injection_guard import guard_untrusted_content
from repositories.chat_repo import list_history
from repositories.user_repo import get_profile
from services.rag import get_document_chunk_hits, search_with_metadata

MAX_ATTACHMENT_CONTEXT_CHARS = 12000
MAX_RETRIEVED_CONTEXT_CHARS = 6000

# ---------- G3：token 预算截断 ----------


def estimate_tokens(text: str) -> int:
    """无分词器的保守估算：CJK 字符 ≈ 1 token/字，其余 ≈ 4 字符/token。

    宁可高估不可低估——截断是成本/崩溃防护，高估只多剪几句旧对话，
    低估会超上下文窗口直接报错。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f")
    other = len(text) - cjk
    return cjk + (other + 3) // 4


HISTORY_OMITTED_NOTE = "（因长度限制，更早的 {n} 条对话已省略，以下仅为最近对话）"


def truncate_history_by_tokens(history: list[dict], budget_tokens: int) -> tuple[list[dict], int]:
    """从新到旧累计 token，超预算即丢弃更早条目；返回（保留历史, 被丢条数）。

    最新一条永远保留（即使单条就超预算）：宁可贵一次，不能把刚发生的
    上下文全剪成失忆。clean_history（前端展示/落库）不受影响，只剪给模型看的部分。
    """
    if not history:
        return [], 0
    kept: list[dict] = []
    used = 0
    for item in reversed(history):
        cost = estimate_tokens(str(item.get("content", "")))
        if kept and used + cost > budget_tokens:
            break
        kept.append(item)
        used += cost
    kept.reverse()
    return kept, len(history) - len(kept)


logger = logging.getLogger("assistant")


@dataclass
class ChatContext:
    """一次对话所需的全部上下文成品。"""

    messages: list[dict]
    clean_history: list[dict]
    persist_text: str
    attachments: list[dict]
    citations: list[dict] = field(default_factory=list)
    has_knowledge_context: bool = False


def normalize_attachments(attachments: list[dict] | None) -> list[dict]:
    normalized: list[dict] = []
    for attachment in attachments or []:
        name = str(attachment.get("name", "")).strip()
        if not name:
            continue
        normalized.append(
            {
                "name": name,
                "kind": attachment.get("kind"),
                "badge": attachment.get("badge"),
                "meta": attachment.get("meta"),
            }
        )
    return normalized


def format_message_content_for_model(content: str, attachments: list[dict] | None = None) -> str:
    normalized_attachments = normalize_attachments(attachments)
    if not normalized_attachments:
        return content

    attachment_names = "、".join(item["name"] for item in normalized_attachments)
    base_content = (content or "").strip() or "用户发送了附件，请结合附件内容处理本条请求。"
    return f"{base_content}\n\n[该条消息附带文件：{attachment_names}]"


def _citation_from_hit(hit: dict, source_fallback: str) -> dict:
    # UI2-④：摘录进引用载荷，前端悬停卡片直接展示，不用再请求后端
    excerpt = (hit.get("content") or "").strip()
    return {
        "source": hit.get("source", source_fallback),
        "document_id": hit.get("document_id"),
        "chunk_index": int(hit.get("chunk_index", 0)),
        "chunk_count": int(hit.get("chunk_count", 0)),
        "distance": hit.get("distance"),
        "excerpt": excerpt[:200],
    }


def _latest_workflow_note(user_id: int, project_id: int | None) -> str:
    """返回最近一次的 Workflow 终态简述—— assistant 应该直接答这轮结果而不是假装它没跑过。"""
    try:
        from repositories import workflow_run_repo

        recent = workflow_run_repo.list_recent(user_id, project_id, limit=1)
        if not recent:
            return ""
        run = recent[0]
        if run.get("status") not in {"succeeded", "failed"}:
            return ""
        nodes = [
            str(n.get("name") or "未命名")
            for n in run.get("draft_snapshot", {}).get("nodes", [])
            if n.get("capability_id")
        ]
        status_word = "完成" if run["status"] == "succeeded" else "失败"
        error_part = f"：{run['error']}" if run.get("error") else ""
        return (
            f"\n\n[Workflow 最近运行状态]：上一次 workflow {status_word}（{run['id']}，"
            f"节点: {', '.join(nodes) or '无'}）。{error_part}"
        )
    except Exception:
        return ""


def build_attachment_context(
    user_id: int,
    project_id: int | None = None,
    attachments: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    normalized_attachments = normalize_attachments(attachments)
    if not normalized_attachments:
        return "", []

    sections: list[str] = []
    citations: list[dict] = []
    consumed = 0
    for attachment in normalized_attachments:
        hits = get_document_chunk_hits(
            filename=attachment["name"],
            user_id=user_id,
            project_id=project_id,
        )
        if not hits:
            continue

        remaining = MAX_ATTACHMENT_CONTEXT_CHARS - consumed
        if remaining <= 0:
            break

        content_parts: list[str] = []
        for hit in hits:
            text = str(hit.get("content", "")).strip()
            if not text:
                continue
            content_parts.append(text)
            citations.append(_citation_from_hit(hit, attachment["name"]))
        content = "\n".join(content_parts).strip()
        if not content:
            continue

        snippet = content[:remaining]
        # C19-①：附件内容是用户上传的不可信数据——投毒文档可能藏指令，
        # 先副注入行再上 Spotlighting 围栏，不再裸拼进 system prompt
        sections.append(guard_untrusted_content(snippet, f"附件 {attachment['name']}"))
        consumed += len(snippet)

    return "\n\n".join(sections), citations


def build_retrieved_context(
    user_id: int,
    message: str,
    project_id: int | None = None,
) -> tuple[str, list[dict]]:
    if not (message or "").strip():
        return "", []

    hits = search_with_metadata(message, n_results=3, user_id=user_id, project_id=project_id)
    if not hits:
        return "", []

    sections: list[str] = []
    citations: list[dict] = []
    for hit in hits:
        content = str(hit.get("content", "")).strip()
        if not content:
            continue
        source = str(hit.get("source", "未知文档"))
        chunk_index = int(hit.get("chunk_index", 0)) + 1
        # C19-①：检索命中的文档片段同样是不可信数据，scrub+围栏双保险
        sections.append(
            guard_untrusted_content(content, f"来源：{source}｜片段 {chunk_index}")
        )
        citations.append(_citation_from_hit(hit, source))

    joined = "\n\n".join(sections).strip()
    return (joined[:MAX_RETRIEVED_CONTEXT_CHARS] if joined else ""), citations


def assemble_messages(
    system_prompt: str,
    history: list[dict],
    message: str,
    attachments: list[dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """纯函数：把各层素材按固定顺序拼成 (messages, clean_history)。"""
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    clean_history: list[dict] = []
    for item in history:
        role = item.get("role")
        content = str(item.get("content", "")).strip()
        normalized_history_attachments = normalize_attachments(item.get("attachments"))
        if role not in {"user", "assistant"} or not content:
            continue
        # 协议垃圾历史行（DSML/<tool_call> 残贴进库的坏轮）不要回喂模型——
        # LLM 会照抄自己说过的野鸡格式，自我教学成坏偏好（真机实证 2026-10-19）
        if "DSML" in content or "<tool_call>" in content:
            continue
        messages.append(
            {
                "role": role,
                "content": format_message_content_for_model(content, normalized_history_attachments),
            }
        )
        history_item: dict = {"role": role, "content": content}
        # id/feedback 只进 clean_history（前端展示与 UI2 操作定位），不进发模型的 messages
        if item.get("id") is not None:
            history_item["id"] = item["id"]
        if item.get("feedback") is not None:
            history_item["feedback"] = item["feedback"]
        if normalized_history_attachments:
            history_item["attachments"] = normalized_history_attachments
        if item.get("citations"):
            history_item["citations"] = item["citations"]
        clean_history.append(history_item)

    normalized_attachments = normalize_attachments(attachments)
    messages.append(
        {
            "role": "user",
            "content": format_message_content_for_model(message, normalized_attachments),
        }
    )
    current_user_history_item: dict = {"role": "user", "content": message}
    if normalized_attachments:
        current_user_history_item["attachments"] = normalized_attachments
    clean_history.append(current_user_history_item)
    return messages, clean_history


async def build_chat_context(
    user_id: int,
    message: str,
    project_id: int | None = None,
    attachments: list[dict] | None = None,
) -> ChatContext:
    """并行构建附件上下文和普通 RAG 上下文，单个分支失败不连坐。"""
    rag_started = time.perf_counter()
    try:
        results: list[object] = await asyncio.gather(
            asyncio.to_thread(build_attachment_context, user_id, project_id, attachments),
            asyncio.to_thread(build_retrieved_context, user_id, message, project_id),
            return_exceptions=True,
        )
        attachment_result, retrieved_result = results
        attachment_context, attachment_citations = (
            attachment_result if isinstance(attachment_result, tuple) else ("", [])
        )
        retrieved_context, retrieved_citations = (
            retrieved_result if isinstance(retrieved_result, tuple) else ("", [])
        )
        for label, result in (("attachment", attachment_result), ("retrieval", retrieved_result)):
            if isinstance(result, Exception):
                logger.warning(
                    "单个知识库上下文分支失败",
                    extra={
                        "evt": "context_branch_error",
                        "branch": label,
                        "error_type": type(result).__name__,
                    },
                )
        context_sections = [item for item in [attachment_context, retrieved_context] if item]
        context = "\n\n".join(context_sections)
        citations = attachment_citations + retrieved_citations
        logger.info(
            "上下文构建完成",
            extra={
                "evt": "context_build",
                "duration_ms": round((time.perf_counter() - rag_started) * 1000, 1),
                "attachment_chars": len(attachment_context),
                "retrieved_chars": len(retrieved_context),
                "citation_count": len(citations),
            },
        )
    except Exception as exc:
        logger.warning(
            "上下文构建失败，降级为无 RAG 上下文: %s",
            type(exc).__name__,
            exc_info=True,
            extra={"evt": "context_build_error", "error_type": type(exc).__name__},
        )
        context = ""
        citations = []

    profile, full_history = await asyncio.gather(
        asyncio.to_thread(get_profile, user_id),
        asyncio.to_thread(list_history, user_id, project_id),
    )
    # 给模型一句“上一轮 workflow 的结论”：问题 2——助手上下文里永远有'跑完了'的信号，
    # 不惠它先揭晓'什么运行结果'再问状态
    latest = _latest_workflow_note(user_id, project_id)
    if latest:
        context = (context or "") + latest
    system_prompt = build_system_prompt(profile, context)
    history = full_history[-config.CHAT_HISTORY_MAX_ITEMS :]
    # G3：条数截断只防疯子，token 截断才防破产——预算内从新到旧保留，
    # 被剪时给模型留一句省略声明，避免它把“没看到”当成“没发生过”
    budget = config.CHAT_HISTORY_TOKEN_BUDGET
    truncated_history, dropped = truncate_history_by_tokens(history, budget)
    if dropped:
        logger.info(
            "历史按 token 预算截断",
            extra={
                "evt": "history_token_truncated",
                "dropped": dropped,
                "kept": len(truncated_history),
                "budget_tokens": budget,
            },
        )
        system_prompt = system_prompt + "\n\n" + HISTORY_OMITTED_NOTE.format(n=dropped)
    messages, _ = assemble_messages(system_prompt, truncated_history, message, attachments)
    # clean_history（前端展示/落库口径）用完整历史，不随模型侧截断缩水
    _, clean_history = assemble_messages(system_prompt, history, message, attachments)
    persist_text = message if (message or "").strip() else "已发送附件"
    clean_history[-1]["content"] = persist_text

    return ChatContext(
        messages=messages,
        clean_history=clean_history,
        persist_text=persist_text,
        attachments=normalize_attachments(attachments),
        citations=citations,
        has_knowledge_context=bool(context),
    )
