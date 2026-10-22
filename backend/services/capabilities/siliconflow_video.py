"""SiliconFlow 视频 Provider（Wan2.2-T2V，E2 视频链路）。

API 形状（真机验证 2026-09-30）：
- POST /v1/video/submit {"model", "prompt", "image_size", "duration"} → {"requestId"}
  ⚠️ prompt 在根级，不是嵌在 input 里（嵌套会报 20016 Prompt field is required）；
- POST /v1/video/status {"requestId"} → {"status", "results": {"videos": [{"url"}]}}
  状态机：InQueue → InProgress → Succeed / Failed；生成需 1~5 分钟；
- 产出是 S3 预签名 URL（24h 过期）——和图像 Provider 同一个坑：必须当场下载
  转存到我们自己的 assets 目录，引用我方持久路径。

关键约束：
- 调用方负责把本函数丢线程池（同步 httpx + 阻塞轮询，不阻塞事件循环）；
- 未配置 SILICONFLOW_API_KEY 时抛 ModelNotConfiguredError，Runtime 转
  model_unavailable 状态——“没接 Provider 就直说”，绝不伪造输出。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from core import config
from schemas.capability import VideoGenerateInput
from services.capabilities.zhipu_text import ModelNotConfiguredError

SUBMIT_URL = "https://api.siliconflow.cn/v1/video/submit"
STATUS_URL = "https://api.siliconflow.cn/v1/video/status"

# 比例 → Wan2.2 支持的原生分辨率（真机验证 1280x720 可用）
_RATIO_TO_SIZE = {
    "16:9": "1280x720",
    "1:1": "960x960",
    "9:16": "720x1280",
}

_DURATION_TO_SECONDS = {
    "5 秒": 5,
    "10 秒": 10,
}

# 轮询节奏与总预算：生成实测 1~5 分钟；10 分钟兜底防止永久挂起
_POLL_INTERVAL_SECONDS = 5
_POLL_TIMEOUT_SECONDS = 600


def _assets_dir() -> Path:
    out = Path(config.DOCUMENT_STORAGE_DIR).parent / "assets"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _submit(prompt: str, image_size: str, duration: int) -> str:
    response = httpx.post(
        SUBMIT_URL,
        headers={
            "Authorization": f"Bearer {config.SILICONFLOW_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.VIDEO_MODEL,
            "prompt": prompt,
            "image_size": image_size,
            "duration": duration,
        },
        timeout=60.0,
    )
    response.raise_for_status()
    request_id = response.json().get("requestId")
    if not request_id:
        raise RuntimeError("视频 API 未返回 requestId")
    return request_id


def _wait_for_video(request_id: str) -> str:
    """阻塞轮询直到终态。返回视频 URL；失败/超时抛 RuntimeError。"""
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        response = httpx.post(
            STATUS_URL,
            headers={
                "Authorization": f"Bearer {config.SILICONFLOW_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"requestId": request_id},
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status")

        if status == "Succeed":
            videos = (payload.get("results") or {}).get("videos") or []
            url = videos[0].get("url") if videos else None
            if not url:
                raise RuntimeError("视频生成成功但未返回链接")
            return url
        if status == "Failed":
            reason = payload.get("reason") or "未知原因"
            raise RuntimeError(f"视频生成失败（{reason}）")

        time.sleep(_POLL_INTERVAL_SECONDS)

    raise RuntimeError("视频生成超时（10 分钟），请稍后重试")


def generate_video(inputs: VideoGenerateInput) -> dict[str, Any]:
    """真实出视频：远程生成 → 轮询到终态 → 立即下载转本地 → 返回本地持久 URL。"""
    if not config.SILICONFLOW_API_KEY:
        raise ModelNotConfiguredError("视频模型尚未配置")

    # 控制信号全走结构化参数；prompt 只承载镜头/画面描述，风格轻量后缀引导
    final_prompt = f"{inputs.prompt}。风格：{inputs.style}。"
    request_id = _submit(
        final_prompt,
        _RATIO_TO_SIZE[inputs.ratio],
        _DURATION_TO_SECONDS[inputs.duration],
    )
    url = _wait_for_video(request_id)

    # 预签名临时 URL 立刻转存 — 不转存明天就是一条死链
    downloaded = httpx.get(url, timeout=300.0, follow_redirects=True)
    downloaded.raise_for_status()
    filename = f"{uuid.uuid4().hex}.mp4"
    path = _assets_dir() / filename
    path.write_bytes(downloaded.content)

    asset_row = {
        "asset_id": f"asset-{uuid.uuid4().hex[:12]}",
        "filename": filename,
        "path": f"/api/assets/{filename}",
        "capability_id": "video.generate",
        "prompt_snapshot": inputs.model_dump(),
        "byte_size": len(downloaded.content),
    }

    return {
        "content": f"![视频](/api/assets/{filename})",
        "asset_path": f"/api/assets/{filename}",
        "asset": asset_row,
        "usage": {
            "model": config.VIDEO_MODEL,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
