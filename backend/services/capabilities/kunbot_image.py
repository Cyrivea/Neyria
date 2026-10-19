"""Kunbot 图像 Provider（OpenAI 兼容 /images/generations，路由 7sref）。

从 SiliconFlow 模块镜像而来，语义对齐：
- 上游 URL 可能是会过期的临时链，必须**当场转存**到我方 assets 目录（只认本地持久路径）；
- 兼容两种返回面：data[0].url（下载转存）或 data[0].b64_json（直接落盘）；
- 未配置 KUNBOT_API_KEY 时抛 ModelNotConfiguredError，Runtime 转 model_unavailable；
- 同步 httpx，调用方负责放进线程池。

模型指向 gpt-image 系列（gemini-* 走另一套 Gemini 请求面，不接）。
"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any

import httpx

from core import config
from schemas.capability import ImageGenerateInput
from services.capabilities.zhipu_text import ModelNotConfiguredError

# gpt-image 支承的尺寸枚举（讲述 retarget 上限）；比例映射取“最接近的官方尺寸”
_RATIO_TO_SIZE = {
    "1:1": "1024x1024",
    "16:9": "1536x1024",
    "9:16": "1024x1536",
    "4:3": "1536x1024",
}


def _assets_dir() -> Path:
    out = Path(config.DOCUMENT_STORAGE_DIR).parent / "assets"
    out.mkdir(parents=True, exist_ok=True)
    return out


def generate_image(inputs: ImageGenerateInput) -> dict[str, Any]:
    """真实出图（Kunbot 网关）：远程生成 → 立即转存本地 → 返回本地持久 URL。"""
    if not config.KUNBOT_API_KEY:
        raise ModelNotConfiguredError("图像模型尚未配置")

    # 控制信号全走结构化参数；prompt 只承載画面描述，味道/色调用后缀轻量引导
    final_prompt = f"{inputs.prompt}。风格：{inputs.style}。色调：{inputs.palette}。"
    response = httpx.post(
        f"{config.KUNBOT_IMAGE_BASE.rstrip('/')}/images/generations",
        headers={
            "Authorization": f"Bearer {config.KUNBOT_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.IMAGE_MODEL_KUNBOT,
            "prompt": final_prompt,
            "size": _RATIO_TO_SIZE[inputs.ratio],
            "n": 1,
        },
        timeout=180.0,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"图像生成失败（网关 HTTP {exc.response.status_code}）"
        ) from exc
    payload = response.json()
    images = payload.get("data")
    if not isinstance(images, list) or not images:
        raise RuntimeError("图像 API 返回了空结果")
    first = images[0] if isinstance(images[0], dict) else {}

    filename = f"{uuid.uuid4().hex}.png"
    path = _assets_dir() / filename
    if first.get("b64_json"):
        path.write_bytes(base64.b64decode(first["b64_json"]))
    elif first.get("url"):
        # 临时 URL 立即转存 —— 不转存明天就是一张破图
        downloaded = httpx.get(first["url"], timeout=120.0, follow_redirects=True)
        downloaded.raise_for_status()
        path.write_bytes(downloaded.content)
    else:
        raise RuntimeError("图像 API 返回了空结果")

    asset_row = {
        "asset_id": f"asset-{uuid.uuid4().hex[:12]}",
        "filename": filename,
        "path": f"/api/assets/{filename}",
        "capability_id": "image.generate",
        "prompt_snapshot": inputs.model_dump(),
    }

    return {
        "content": f"![曲绘](/api/assets/{filename})",
        "asset_path": f"/api/assets/{filename}",
        "asset": asset_row,
        "usage": {
            "model": config.IMAGE_MODEL_KUNBOT,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
