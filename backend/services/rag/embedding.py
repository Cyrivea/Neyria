import logging
import time
from typing import Any

import httpx

from core import config
from core.config import EMBEDDING_API_URL, EMBEDDING_MODEL, SILICONFLOW_API_KEY

logger = logging.getLogger("rag.embedding")


def _extract_vectors(payload: dict[str, Any], context: str) -> list[list[float]]:
    """从 SiliconFlow OpenAI 兼容响应中提取向量，并校验结构。"""
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"{context}：embedding API 返回了空结果")

    ordered_data = sorted(
        data,
        key=lambda item: item.get("index", 0) if isinstance(item, dict) else 0,
    )
    vectors: list[list[float]] = []
    for item in ordered_data:
        if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
            raise RuntimeError(f"{context}：embedding API 返回了无效向量")
        vectors.append(item["embedding"])
    return vectors


def _embed(inputs: str | list[str], context: str) -> list[list[float]]:
    """Provider 分流：siliconflow（默认，走 bge-m3） / dashscope（备胎，欠费时跟下家换小黄鱼）。

    2026-10-29 背景：SiliconFlow 欠费 402 干塌了整个 RAG，但没有退路永远不行——
    这个流亡政备由 EMBEDDING_PROVIDER 选择，配置文件环境变量即是逃生舱。
    """
    if config.EMBEDDING_PROVIDER == "dashscope":
        from services.rag.embedding_dashscope import _embed as dash_embed

        return dash_embed(inputs, context)

    if not SILICONFLOW_API_KEY:
        raise RuntimeError("未配置 SILICONFLOW_API_KEY，RAG 向量检索暂不可用")

    try:
        response = httpx.post(
            EMBEDDING_API_URL,
            headers={
                "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": EMBEDDING_MODEL, "input": inputs},
            timeout=60.0,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"{context}：embedding API 返回了无效 JSON")
        return _extract_vectors(payload, context)
    except httpx.HTTPStatusError as exc:
        # 只把状态码和服务商返回的错误文本交给上层，不记录 API key。
        detail = exc.response.text[:500]
        raise RuntimeError(
            f"{context}失败（SiliconFlow HTTP {exc.response.status_code}）：{detail}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"{context}失败：无法连接 SiliconFlow embedding 服务") from exc


def get_embedding(text: str) -> list[float]:
    started = time.perf_counter()
    try:
        vectors = _embed(text, "查询向量化")
        if len(vectors) != 1:
            raise RuntimeError(f"查询向量化：API 返回了 {len(vectors)} 个向量，预期 1 个")
    except Exception as exc:
        logger.error(
            "查询向量化失败: %s", type(exc).__name__,
            extra={"evt": "embed_query_error", "error_type": type(exc).__name__,
                   "text_chars": len(text)},
        )
        raise

    logger.info(
        "查询向量化完成",
        extra={"evt": "embed_query", "provider": "siliconflow", "model": EMBEDDING_MODEL,
               "text_chars": len(text),
               "duration_ms": round((time.perf_counter() - started) * 1000, 1)},
    )
    return vectors[0]


def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    started = time.perf_counter()
    try:
        # BAAI/bge-m3 支持 input=list[str]，一次请求生成多个文档块向量。
        embeddings = _embed(texts, "批量向量化")
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"批量向量化：API 返回了 {len(embeddings)} 个向量，预期 {len(texts)} 个"
            )
    except Exception as exc:
        logger.error(
            "批量向量化失败: %s", type(exc).__name__,
            extra={"evt": "embed_batch_error", "error_type": type(exc).__name__,
                   "total_chunks": len(texts)},
        )
        raise

    logger.info(
        "批量向量化完成",
        extra={"evt": "embed_batch", "provider": "siliconflow", "model": EMBEDDING_MODEL,
               "chunks": len(texts),
               "duration_ms": round((time.perf_counter() - started) * 1000, 1)},
    )
    return embeddings
