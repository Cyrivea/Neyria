"""意图分类器（C16）三级漏斗。

Layer1 规则：零成本、判错零风险才敢动；，如"12×34 = 多少"必须走 calculate。
Layer2 便宜模型：输出结构化 {intent, confidence, needs_tools, needs_rag}；
         置信度低于阈值一律标 unknown 直送主模型（绝不硬猜）。
Layer3 主模型兜底：unknown / 复杂场景 → 现状（prompt 自判，可选工具全给）。

开关：INTENT_CLASSIFIER_ENABLED=off 时完全走老路（prompt-only），一行回滚。
度量：每次分类落日志（pattern/confidence/source），攒多了换离线版本。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from core import config

logger = logging.getLogger("intent")

# ---------- 意图枚举 & 工具约束表 ----------

INTENT_CREATE = "create"          # 新建/生成，必须 workflow
INTENT_RUN = "run"                # 用户要求执行已有流程，必须 run_current_workflow
INTENT_MODIFY = "modify"          # 改参数、改主题、复核，必须 read+configure
INTENT_STATUS = "status"          # 进度/跑了吗/盯梢，必须 get_workflow_run_status
INTENT_CLARIFY = "clarify"        # 暧昧模糊不清
INTENT_KB_QUESTION = "kb"         # 文档/知识库
INTENT_WEB_SEARCH = "web"         # 互联网查询
INTENT_CALC = "calc"              # 数学计算
INTENT_CHAT = "chat"              # 纯闲聊/副作用禁止
INTENT_UNKNOWN = "unknown"

# 语义意图 → 给主模型保留的 workflow 工具白名单（其他工具还在）
# 不在表中的意图 = 不限制（unknown）
_TOOLS_BY_INTENT: dict[str, set[str]] = {
    INTENT_CREATE: {
        "configure_lyrics_workflow",
        "configure_image_workflow",
        "run_current_workflow",
        "read_workflow_state",
        "clear_workflow_draft",
        "wait_for_workflow_completion",
        "get_workflow_run_status",
        "generate_lyrics_block",
    },
    INTENT_RUN: {
        "read_workflow_state",
        "run_current_workflow",
        "wait_for_workflow_completion",
        "get_workflow_run_status",
    },
    INTENT_MODIFY: {
        "read_workflow_state",
        "configure_lyrics_workflow",
        "configure_image_workflow",
        "run_current_workflow",
        "wait_for_workflow_completion",
        "get_workflow_run_status",
        "clear_workflow_draft",
    },
    INTENT_STATUS: {
        "read_workflow_state",
        "get_workflow_run_status",
        "wait_for_workflow_completion",
    },
    INTENT_KB_QUESTION: {"read_workflow_state", "get_workflow_run_status"},
    INTENT_WEB_SEARCH: {"search_web"},
    INTENT_CALC: {"calculate"},
    INTENT_CHAT: set(),  # 关闭一切副作用工具
    # clarify / unknown 不在表 → 兜底全开
}


def tools_for_intent(intent: str, include_all: bool = False) -> set[str]:
    """返回该意图允许的工具集合；include_all=True 表示不限制。"""
    if include_all or intent not in _TOOLS_BY_INTENT or intent in {
        INTENT_UNKNOWN,
        INTENT_CLARIFY,
    }:
        return set()
    return _TOOLS_BY_INTENT[intent]


# ---------- Layer 1：规则（零成本秒判） ----------

# 安全规则：判错率为零的才进门（明确的已编码词）
_RULES: list[tuple[re.Pattern, str]] = [
    # 计算问"12*34 等于多少"
    (re.compile(r"\d+[\s]*[-+*/x×\^][\s]*\d+"), INTENT_CALC),
    # "跑得怎么样了" / "进度" / "跑完了吗" / "出来了吗" / "继续"
    (
        re.compile(
            r"跑得(?:怎么样|如何|吧|吗|没)|进度|跑完了吗|出来了吗|继续|盯梢|跑完"
        ),
        INTENT_STATUS,
    ),
    # KB：“我…的…在哪” “文档…有没有” “知识库里说了什么”
    (
        re.compile(r"我(?:的)?(.+)?(?:在哪|告诉我|说说|文件|资料|记录)"),
        INTENT_KB_QUESTION,
    ),
    # 明确清空
    (re.compile(r"(清空|清掉|重建|重做|重来|全部删除|删除整个)"), INTENT_MODIFY),
]

# Layer 2 置信度门限——低于它不硬导
CONFIDENCE_GATE = 0.65


# ---------- Layer 2：便宜模型结构化分类 ----------


def _rule_classify(message: str) -> tuple[str | None, float]:
    for pat, intent in _RULES:
        if pat.search(message):
            return intent, 0.95
    return None, 0.0


async def _cheap_classify(
    message: str,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """便宜 LLM 分类器：结构化输出。契约强制 {intent, confidence}。"""
    from services.llm import get_chat_client

    llm_client = get_chat_client()
    if llm_client is None:
        return None

    sys_prompt = (
        "你是一个意图分类器。用户消息会送到你手里，你只输出 JSON："
        '{"intent": "create|run|modify|status|kb|web|calc|chat|clarify|unknown", "confidence": 0.0-1.0, "reason": "不超过20字"}。'
        "intent 对照含义：create=新建生成任务，run=执行已有工作流，modify=调整参数，"
        "status=查进度，kb=文档/相册/知识库问答，web=需要联网，calc=数学计算，"
        "chat=纯闲聊（如早安、你好），clarify=用户意图不明需追问，unknown=不确定。"
        "confidence < 0.65 时把 intent 改为 unknown。不要输出任何其他内容。"
    )
    try:
        intent_reply = await _to_thread(
            llm_client.complete,
            model=config.CHAT_MODEL,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": message},
            ],
        )
        text = intent_reply.content
        if not text:
            return None
        obj = json.loads(text.strip().replace("```json", "").replace("```", "").strip())
        return obj if isinstance(obj, dict) else None
    except Exception as exc:
        logger.warning("便宜分类器失败，不改路线走兜底", extra={"error_type": type(exc).__name__})
        return None


async def _to_thread(fn, **kw):

    return await asyncio.to_thread(fn, **kw)


# ---------- 对外契约 ----------

_RESULT_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_MAX = 512


async def classify(
    message: str,
    has_knowledge_context: bool,
    history_len: int = 0,
    enable_classifier: bool | None = None,
) -> dict[str, Any]:
    """分类用户意图并返回结构化结果。

    enable_classifier=None 时按 config 走（默认开启）。
    结果结构：{
      "intent": str, "confidence": float,
      "source": "rule" | "llm" | "fallback",
      "allow_tools": set[str] (可能为空集 = 不限制),
    }
    """
    enabled = (
        enable_classifier
        if enable_classifier is not None
        else config.INTENT_CLASSIFIER_ENABLED
    )
    if not enabled:
        return {
            "intent": INTENT_UNKNOWN,
            "confidence": 1.0,
            "source": "disabled",
            "allow_tools": set(),
        }

    cache_key = f"{message}|{history_len}"
    if cache_key in _RESULT_CACHE:
        return _RESULT_CACHE[cache_key]

    if len(_RESULT_CACHE) >= _CACHE_MAX:
        _RESULT_CACHE.clear()

    # Layer1 规则：能秒判就直接出结果
    rule_intent, rule_conf = _rule_classify(message)
    if rule_intent is not None:
        out = {
            "intent": rule_intent,
            "confidence": rule_conf,
            "source": "rule",
            "allow_tools": tools_for_intent(rule_intent),
        }
        logger.info(
            "意图分类",
            extra={"evt": "intent_classified", "method": "rule", "intent": rule_intent},
        )
        _RESULT_CACHE[cache_key] = out
        return out

    # Layer2 便宜模型：兜底、成本高但平行步少
    result = await _cheap_classify(message)
    if result is None:
        return {
            "intent": INTENT_UNKNOWN,
            "confidence": 1.0,
            "source": "fallback",
            "allow_tools": set(),
        }

    intent = result.get("intent", INTENT_UNKNOWN)
    confidence = float(result.get("confidence", 0.0) or 0.0)
    if confidence < CONFIDENCE_GATE:
        intent = INTENT_UNKNOWN

    out = {
        "intent": intent,
        "confidence": confidence,
        "source": "llm",
        "allow_tools": tools_for_intent(intent),
    }
    logger.info(
        "意图分类",
        extra={
            "evt": "intent_classified",
            "method": "llm",
            "intent": intent,
            "confidence": confidence,
        },
    )
    _RESULT_CACHE[cache_key] = out
    return out


def reset_cache() -> None:
    _RESULT_CACHE.clear()
