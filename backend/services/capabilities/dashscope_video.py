"""阿里百炼（DashScope）视频 Provider——T2V 云端兜底档。

与 SiliconFlow/ComfyUI 三段的同构语义（提交任务 → 轮询终态 → 下载转存）：
- POST {TASK_BASE}/services/aigc/video-generation/video-synthesis（X-DashScope-Async: enable）
  → output.task_id；预期约 40s 拿终态（真机 2026-10-29 验证 5s turbo 返 case 40s）
- GET  {TASK_BASE}/tasks/{task_id} → output.video_url（预签名 OSS 临时 URL，过期转储是强的）
- GET  video_url 下载转 utf8 assets 目录，usage.usage.video_duration 上报秒数（按秒计费）

计费：wan2.2-t2v-plus 480P ¥0.14/秒、turbo 480P/720P ¥0.24/秒、默认型号为首冲 200 秒
免费额度（距账期内零元）的 wanx2.1-t2v-turbo。成本按「真实秒数」记 provider_cost。

时长欺骗红线（真实事故好好学习）：wan2.2/turbo 系列**只食守诺 5 秒自产**（参数 duration
超 2 的值都会被 InvalidParameter 拒）；不支持新步一声不吭绞你参数——调用方请求「10 秒」
时我们**不偷偷发两次而是如实拒绝**（后续需求要长视频再接 wan2.7 波段家族）。

调用方负责把本函数丢线程池（同步 httpx + 阻塞轮询；与另两家的传统一致）。
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

_RATIO_TO_SIZE = {
    "16:9": "1280*720",
    "1:1": "960*960",
    "9:16": "720*1280",
}
# turbo 在百炼侧可生 480P / 720P；映射表以「画幅档位英文名」对接上游 API size 参数

_POLL_INTERVAL_SECONDS = 3
_POLL_TIMEOUT_SECONDS = 600


def _assets_dir() -> Path:
    out = Path(config.DOCUMENT_STORAGE_DIR).parent / "assets"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _submit(prompt: str, size: str) -> str:
    response = httpx.post(
        f"{config.DASHSCOPE_TASK_BASE}/services/aigc/video-generation/video-synthesis",
        headers={
            "Authorization": f"Bearer {config.DASHSCOPE_API_KEY}",
            "X-DashScope-Async": "enable",
            "Content-Type": "application/json",
        },
        json={
            "model": config.VIDEO_MODEL_DASH,
            "input": {"prompt": prompt},
            "parameters": {"size": size, "prompt_extend": False},
        },
        timeout=60.0,
    )
    response.raise_for_status()
    task_id = (response.json().get("output") or {}).get("task_id")
    if not task_id:
        raise RuntimeError("百炼视频 API 未返回 task_id")
    return str(task_id)


def _wait_for_video(task_id: str) -> tuple[str, int]:
    """轮询到终态。返回 (video_url, video_duration 秒数)；失败/超时抛 RuntimeError。"""
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    url = f"{config.DASHSCOPE_TASK_BASE}/tasks/{task_id}"
    while time.monotonic() < deadline:
        r = httpx.get(url, headers={"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"}, timeout=30.0)
        r.raise_for_status()
        output = r.json().get("output") or {}
        status = output.get("task_status")
        if status == "SUCCEEDED":
            video_url = output.get("video_url")
            if not video_url:
                raise RuntimeError("视频生成成功但百炼未返回链接")
            usage = output.get("usage") or {}
            duration = int(usage.get("video_duration") or 5)
            return str(video_url), duration
        if status in ("FAILED", "CANCELED"):
            msg = output.get("message") or output.get("code") or "未知原因"
            raise RuntimeError(f"百炼视频生成失败（{msg}）")
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise RuntimeError("百炼视频生成超时（10 分钟），请稍后重试")


def generate_video(inputs: VideoGenerateInput) -> dict[str, Any]:
    """文生视频（T2V）：百炼上提任务 → 轮询 → 预签名 URL 立转存到资产目录。"""
    if not config.DASHSCOPE_API_KEY:
        raise ModelNotConfiguredError("视频模型尚未配置")

    # wan2.2 / wanx2.1 turbo 出厂固定 5 秒——不支持自定义时长：请求 10 秒时如实拒绝，
    # 绝不偷偷截短、不谎报长度。需长视频走 wan2.7 系列（为后备档预存）。
    if inputs.duration != "5 秒":
        raise ModelNotConfiguredError(f"百炼 wan 视频默认档仅产 5 秒成品（你点了 {inputs.duration}）")

    size = _RATIO_TO_SIZE.get(inputs.ratio)
    if size is None:
        raise RuntimeError(f"未知画面比例: {inputs.ratio}")

    final_prompt = f"{inputs.prompt}。风格：{inputs.style}。"
    task_id = _submit(final_prompt, size)
    video_url, duration_seconds = _wait_for_video(task_id)

    downloaded = httpx.get(video_url, timeout=300.0, follow_redirects=True)
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
        # 云端百炼按秒计费：usage 里的 model 写真实档位 + 秒数 = 成本会计可复现
        "usage": {
            # model 列保持纯型号名——provider_cost 按名计费；真实秒数安防进 token 列
            "model": config.VIDEO_MODEL_DASH,
            "prompt_tokens": 0,
            "completion_tokens": duration_seconds,
            "total_tokens": duration_seconds,
        },
    }
