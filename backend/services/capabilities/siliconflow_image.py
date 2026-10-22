"""SiliconFlow 图像 Provider（E2 首个真实图像后端）。

用 Kwai-Kolors/Kolors：该账号下 free tier 可用且中文提示词友好
（FLUX.1-schnell 在此账号被 disable，实测 403 Model disabled）。

关键约束（吃过的亏都在注释里）：
- 上游返回的是**临时签名 URL**（s3 预签名，会过期）——必须当场下载转存到
  我们自己的 assets 目录，引用我方持久路径，不然图片半天就变 403；
- 调用方负责把本函数丢线程池（同步 httpx，不阻塞事件循环）；
- 未配置 SILICONFLOW_API_KEY 时抛 ModelNotConfiguredError，Runtime 转
  model_unavailable 状态——“没接 Provider 就直说”，绝不伪造输出。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx

from core import config
from schemas.capability import ImageGenerateInput
from services.capabilities.zhipu_text import ModelNotConfiguredError

API_URL = "https://api.siliconflow.cn/v1/images/generations"

_RATIO_TO_SIZE = {
    "1:1": "1024x1024",
    "16:9": "1344x768",
    "9:16": "768x1344",
    "4:3": "1152x864",
}


def _assets_dir() -> Path:
    out = Path(config.DOCUMENT_STORAGE_DIR).parent / "assets"
    out.mkdir(parents=True, exist_ok=True)
    return out


def generate_image(inputs: ImageGenerateInput) -> dict[str, Any]:
    """真实出图：远程生成 → 立即下载转本地 → 返回本地持久 URL。"""
    if not config.SILICONFLOW_API_KEY:
        raise ModelNotConfiguredError("图像模型尚未配置")

    # 控制信号全走结构化参数；prompt 只承載画面描述，味道/色调用后缀轻量引导
    final_prompt = f"{inputs.prompt}。风格：{inputs.style}。色调：{inputs.palette}。"
    response = httpx.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {config.SILICONFLOW_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.IMAGE_MODEL,
            "prompt": final_prompt,
            "image_size": _RATIO_TO_SIZE[inputs.ratio],
            "batch_size": 1,
        },
        timeout=180.0,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"图像生成失败（SiliconFlow HTTP {exc.response.status_code}）"
        ) from exc
    payload = response.json()
    images = payload.get("images")
    if not isinstance(images, list) or not images or not images[0].get("url"):
        raise RuntimeError("图像 API 返回了空结果")
    url = images[0]["url"]

    # 预签名临时 URL 立刻转存 — 不转存明天就是一张破图
    downloaded = httpx.get(url, timeout=120.0, follow_redirects=True)
    downloaded.raise_for_status()
    filename = f"{uuid.uuid4().hex}.png"
    path = _assets_dir() / filename
    path.write_bytes(downloaded.content)

    # 关灯资产表登记：文件名是不可信任的票据，身份还是必须落账才能回答
    # “这图是哪个用户哪个项目哪个 run 调的，花了多少”——usage_events 里那条就是这次的
    import uuid as _uuid

    asset_row = {
        "asset_id": f"asset-{_uuid.uuid4().hex[:12]}",
        "filename": filename,
        "path": f"/api/assets/{filename}",
        "capability_id": "image.generate",
        "prompt_snapshot": inputs.model_dump(),
        "byte_size": len(downloaded.content),
    }

    return {
        "content": f"![曲绘](/api/assets/{filename})",
        "asset_path": f"/api/assets/{filename}",
        "asset": asset_row,
        "usage": {
            "model": config.IMAGE_MODEL,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
