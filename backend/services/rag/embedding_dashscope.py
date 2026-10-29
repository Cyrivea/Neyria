"""百炼 DashScope embedding Provider（text-embedding-v4，RAG 备胎复活通道）。

与 SiliconFlow `embedding.py` 共用公开函数，services/rag/embedding.py 按
EMBEDDING_PROVIDER 分流。百炼响应形状与 OpenAI 兼容：

    POST {DASHSCOPE_EMBEDDING_API_URL}
    Body: {"model": "text-embedding-v4", "input": {"texts": [...]}, "parameters": {"dimension": 1024}}
    → {"output": {"embeddings": [{"text_index": 0, "embedding": [...]}, ...]}}

维度默认 1024（与 BGE-M3 一致），env 可上 2048/3072。
"""

from __future__ import annotations

import httpx

from core import config


def _extract(payload: dict, context: str) -> list[list[float]]:
    items = (payload.get("output") or {}).get("embeddings")
    if not isinstance(items, list) or not items:
        raise RuntimeError(f"{context}：百炼 embedding API 返回了空结果")
    ordered = sorted(items, key=lambda x: x.get("text_index", 0) if isinstance(x, dict) else 0)
    vectors = []
    for item in ordered:
        vec = item.get("embedding")
        if not isinstance(vec, list):
            raise RuntimeError(f"{context}：百炼 embedding API 返回了无效向量")
        vectors.append(vec)
    return vectors


def _embed(inputs: str | list[str], context: str) -> list[list[float]]:
    if not config.DASHSCOPE_API_KEY:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY，RAG 向量检索暂不可用")

    texts = [inputs] if isinstance(inputs, str) else inputs
    response = httpx.post(
        config.DASHSCOPE_EMBEDDING_API_URL,
        headers={
            "Authorization": f"Bearer {config.DASHSCOPE_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.DASHSCOPE_EMBEDDING_MODEL,
            "input": {"texts": texts},
            "parameters": {"dimension": config.DASHSCOPE_EMBEDDING_DIM},
        },
        timeout=60.0,
    )
    response.raise_for_status()
    payload = response.json()
    vec = _extract(payload, context)
    if len(vec) != len(texts):
        raise RuntimeError(f"{context}：响应向量数与请求文本数对不上（{len(vec)} vs {len(texts)}）")
    return vec


def get_embedding(text: str) -> list[float]:
    return _embed(text, "查询向量化")[0]


def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    return _embed(texts, "批量文档向量化")
