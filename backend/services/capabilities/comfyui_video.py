"""ComfyUI 本地视频 Provider（LTX-Video 2B 蒸馏版，自建算力）。

与云端 Provider 同构的「提交→轮询→下载转存」三段式：
- POST /prompt {prompt: workflow_json} → {"prompt_id"}（workflow 走 API 格式，
  模板存 services/capabilities/templates/ltxv_t2v.json，本次只改 4 个字段：
  节点4 正向提示词 / 节点6 宽×高×帧数 / 节点7 随机种子）；
- 轮询 GET /history/{prompt_id} 直至 status.completed；
- 从输出节点信息里取出 /view?filename=... 把 mp4 拉回本地——ComfyUI 的输出
  目录在生成机上，不经过 HTTP 转存的话远端重启/清理就丢。

本地算力的账务语义（这是本 Provider 与云端的核心差异，面试可讲）：
- 上游成本按 0 记账（电费不核算，显式 price=0 登记在 provider_cost 表里）；
- 但 usage_events 一条不落照记（model=LTX-Video-2B-distilled/local）——
  用量看的不是钱，是「平台替你跑了多少次」；
- COMFYUI 不在线时 ModelNotConfiguredError 拒绝——「本地算力离线」堂堂正正上报。

主机网络：WSL 里 COMFYUI_BASE_URL 填 Windows 宿主 IP（见 core/config.py 注释）；
ComfyUI 需以 --listen 0.0.0.0 启动而非默认的 127.0.0.1。

调用方负责把本函数丢线程池（同步 httpx + 阻塞轮询）。
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from core import config
from schemas.capability import VideoGenerateInput
from services.capabilities.zhipu_text import ModelNotConfiguredError

logger = logging.getLogger("capabilities.comfyui_video")

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "ltxv_t2v.json"
_I2V_TEMPLATE_PATH = Path(__file__).parent / "templates" / "ltxv_i2v.json"

# I2V 首帧引导强度：1.0 = 首帧必须接近源图（"把这张图动起来"）；调低会给模型更多自由发挥
_I2V_STRENGTH = float(os.getenv("LTXV_I2V_STRENGTH", "1.0"))

# LTX-Video 硬约束（来自 ComfyUI 龙工作流本体）：
# - 宽高必须能被 32 乾除；本地消费卡建议从 640x384 起步而不是 1280x720；
# - 帧数必须满足 8n+1：24fps 下 5 秒=121 帧、10 秒=241 帧；
# - 蒸馏版 steps 4~8 收益趋平，保守取 8。
_RATIO_TO_SIZE = {
    "16:9": (768, 512),
    "1:1": (512, 512),
    "9:16": (512, 768),
}
_DURATION_TO_FRAMES = {
    "5 秒": 121,
    "10 秒": 241,
}
_FPS = 24
_POLL_INTERVAL_SECONDS = 2

# 帧率轻度提示词指导 LTX 的镜头/光影细节偏好（英描述它不挑食，中文提示需包装）
_PROMPT_HINT = (
    "The video has cinematic photography, smooth motion, detailed lighting and vivid colors."
)


def _assets_dir() -> Path:
    out = Path(config.DOCUMENT_STORAGE_DIR).parent / "assets"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _patch_common(workflow: dict[str, Any], prompt: str, style: str, duration: str) -> None:
    """两份模板共用的原位打参：正/负提示词、帧数、随机种子、帧率、输出前缀。"""
    workflow["4"]["inputs"]["text"] = f"{prompt}. Style: {style}. {_PROMPT_HINT}"
    workflow["7"]["inputs"]["noise_seed"] = random.randrange(1, 2**31)
    workflow["13"]["inputs"]["fps"] = float(_FPS)
    workflow["14"]["inputs"]["filename_prefix"] = f"neyria/{uuid.uuid4().hex[:10]}"
    # i2v 模板的潜空间在节点 15（LoadImage 占了 6）；t2v 模板在节点 6（EmptyLTXVLatentVideo）
    latent_node = "15" if "15" in workflow else "6"
    workflow[latent_node]["inputs"]["length"] = _DURATION_TO_FRAMES[duration]


def _load_workflow(prompt: str, style: str, ratio: str, duration: str) -> dict[str, Any]:
    """文生视频模板（t2v）：节点 4=提示词、6=潜空间（宽高×帧数）、7=种子。"""
    workflow: dict[str, Any] = json.loads(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    _patch_common(workflow, prompt, style, duration)
    width, height = _RATIO_TO_SIZE[ratio]
    workflow["6"]["inputs"]["width"] = width
    workflow["6"]["inputs"]["height"] = height
    return workflow


def _sniff_image_size(data: bytes) -> tuple[int, int] | None:
    """只读文件头拿像素宽×高（PNG IHDR / JPEG SOF，互不依赖 Pillow）。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        import struct

        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        pos = 2
        while pos + 9 < len(data):
            if data[pos] != 0xFF:
                pos += 1
                continue
            marker = data[pos + 1]
            if marker in (0xC0, 0xC1, 0xC2) and pos + 9 < len(data):
                import struct

                height, width = struct.unpack(">HH", data[pos + 5 : pos + 9])
                return (width, height)
            seg_len = int.from_bytes(data[pos + 2 : pos + 4])
            if seg_len < 2:
                break
            pos += 2 + seg_len
    return None


def _snap32(value: int) -> int:
    """钳进 32 倍数、不小于 64（LTXV 硬性约束）。"""
    return max(64, min(16384, value // 32 * 32))


def _i2v_size(width: int, height: int) -> tuple[int, int]:
    """保源图纵横比缩到最长边不超过 768，再落 32 格——I2V 不认 ratio 枚举，从图本体出发。"""
    scale = min(1.0, 768 / max(width, height))
    return _snap32(int(width * scale)), _snap32(int(height * scale))


def _upload_image(filename_hint: str, data: bytes, content_type: str) -> str:
    """把本地资产推进 ComfyUI 的 input 目录，返回 LoadImage 认的文件名。"""
    # trust_env=False：内网地址绝不能过代理——环境变量里一旦被注入了 HTTP(S)_PROXY，
    # 内网请求若走代理会 502/超时（真机实证 2026-10-28）。
    response = httpx.post(
        f"{config.COMFYUI_BASE_URL}/upload/image",
        files={"image": (filename_hint, data, content_type)},
        data={"type": "input", "overwrite": "true"},
        timeout=60.0,
        trust_env=False,
    )
    response.raise_for_status()
    name = response.json().get("name")
    if not name:
        raise RuntimeError("ComfyUI 上传图像未返回文件名")
    return str(name)


def _load_i2v_workflow(
    prompt: str, style: str, duration: str, uploaded_name: str, width: int, height: int
) -> dict[str, Any]:
    """图生视频模板（i2v）：节点 4=提示词、6=LoadImage、15=ImgToVideo（宽高×帧数）。"""
    workflow: dict[str, Any] = json.loads(_I2V_TEMPLATE_PATH.read_text(encoding="utf-8"))
    _patch_common(workflow, prompt, style, duration)
    workflow["6"]["inputs"]["image"] = uploaded_name
    workflow["15"]["inputs"]["width"] = width
    workflow["15"]["inputs"]["height"] = height
    workflow["15"]["inputs"]["strength"] = _I2V_STRENGTH
    return workflow


def _submit(workflow: dict[str, Any]) -> str:
    response = httpx.post(
        f"{config.COMFYUI_BASE_URL}/prompt",
        json={"prompt": workflow, "client_id": f"neyria-{uuid.uuid4().hex[:8]}"},
        timeout=30.0,
        trust_env=False,
    )
    response.raise_for_status()
    prompt_id = response.json().get("prompt_id")
    if not prompt_id:
        raise RuntimeError("ComfyUI 未返回 prompt_id")
    return str(prompt_id)


def _pick_video_file(outputs: dict[str, Any]) -> dict[str, str]:
    """从 history 的 outputs 分支里找到那个视频文件描述（SaveVideo 节点产物）。"""
    for node_out in outputs.values():
        for key in ("videos", "gifs", "images"):
            for item in node_out.get(key) or []:
                filename = str(item.get("filename", ""))
                if filename.lower().endswith((".mp4", ".webm")):
                    return {
                        "filename": filename,
                        "subfolder": str(item.get("subfolder", "")),
                        "type": str(item.get("type", "output")),
                    }
    raise RuntimeError("ComfyUI 输出里没有找到视频文件")


def _wait_for_video(prompt_id: str) -> dict[str, str]:
    """阻塞轮询 /history 直至终态。返回 {filename, subfolder, type}；失败抛 RuntimeError。"""
    deadline = time.monotonic() + config.COMFYUI_TIMEOUT_SECONDS
    url = f"{config.COMFYUI_BASE_URL}/history/{prompt_id}"
    while time.monotonic() < deadline:
        response = httpx.get(url, timeout=30.0, trust_env=False)
        # 任务还在队列里时 /history 返回 200 但 body 是 {}——不是错误，等下一轮
        if response.status_code == 200:
            entry = response.json().get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                if status.get("completed"):
                    return _pick_video_file(entry.get("outputs") or {})
                if status.get("status_str") == "error":
                    messages = [
                        m[1].get("exception_message", "")
                        for m in status.get("messages", [])
                        if isinstance(m, list) and len(m) > 1 and isinstance(m[1], dict)
                    ]
                    raise RuntimeError(f"ComfyUI 生成失败（{'; '.join(t for t in messages if t) or '未知原因'}）")
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"ComfyUI 生成超时（{config.COMFYUI_TIMEOUT_SECONDS}s），请稍后重试")


def _download(file_ref: dict[str, str]) -> bytes:
    params = {
        "filename": file_ref["filename"],
        "subfolder": file_ref["subfolder"],
        "type": file_ref["type"],
    }
    response = httpx.get(
        f"{config.COMFYUI_BASE_URL}/view", params=params, timeout=300.0, follow_redirects=True, trust_env=False
    )
    response.raise_for_status()
    return response.content


def _resolve_source_image(source_asset_row: dict[str, Any]) -> tuple[bytes, int, int, str]:
    """从本地资产目录取出源图字节与原生分辨。（调用方已完成属主校验。）"""
    path = _assets_dir() / source_asset_row["filename"]
    if not path.exists():
        raise RuntimeError(f"来源图片资产文件不在磁盘上：{source_asset_row['filename']}")
    data = path.read_bytes()
    size = _sniff_image_size(data)
    if size is None:
        raise RuntimeError("来源图片无法识别为 PNG/JPEG，不能作为图生视频首帧")
    return data, size[0], size[1], (source_asset_row.get("content_type") or "image/png")


def generate_video(
    inputs: VideoGenerateInput,
    source_asset_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """真实出视频：本地 GPU 生成 → 轮询到终态 → 拉回 mp4 → 本地资产落库。

    source_asset_row 为 None → 文生视频（t2v 模板）；给出 → 图生视频（i2v：
    源图作首帧引导，row 必须带 kind=image 且文件存在）。
    """
    if not config.COMFYUI_BASE_URL:
        raise ModelNotConfiguredError("视频模型尚未配置")

    if source_asset_row is None:
        workflow = _load_workflow(inputs.prompt, inputs.style, inputs.ratio, inputs.duration)
    else:
        data, raw_w, raw_h, content_type = _resolve_source_image(source_asset_row)
        width, height = _i2v_size(raw_w, raw_h)
        uploaded = _upload_image(source_asset_row["filename"], data, content_type)
        workflow = _load_i2v_workflow(
            inputs.prompt, inputs.style, inputs.duration, uploaded, width, height
        )
        logger.info(
            "图生视频向 ComfyUI 提交",
            extra={
                "evt": "comfyui_i2v_submit",
                "source": source_asset_row["filename"],
                "size": f"{width}x{height}",
            },
        )
    prompt_id = _submit(workflow)
    file_ref = _wait_for_video(prompt_id)
    video_bytes = _download(file_ref)

    filename = f"{uuid.uuid4().hex}.mp4"
    path = _assets_dir() / filename
    path.write_bytes(video_bytes)

    asset_row = {
        "asset_id": f"asset-{uuid.uuid4().hex[:12]}",
        "filename": filename,
        "path": f"/api/assets/{filename}",
        "capability_id": "video.generate",
        "prompt_snapshot": inputs.model_dump(),
        "byte_size": len(video_bytes),
    }

    return {
        "content": f"![视频](/api/assets/{filename})",
        "asset_path": f"/api/assets/{filename}",
        "asset": asset_row,
        "usage": {
            "model": config.VIDEO_MODEL_COMFYUI,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
