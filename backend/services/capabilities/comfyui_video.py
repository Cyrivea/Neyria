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
import random
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from core import config
from schemas.capability import VideoGenerateInput
from services.capabilities.zhipu_text import ModelNotConfiguredError

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "ltxv_t2v.json"

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


def _load_workflow(prompt: str, style: str, ratio: str, duration: str) -> dict[str, Any]:
    """读模板工作流，原位打参。键名/节点 id 锚定 LTX-Video t2v 模板本体。"""
    workflow: dict[str, Any] = json.loads(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    width, height = _RATIO_TO_SIZE[ratio]
    frames = _DURATION_TO_FRAMES[duration]

    # LTX 偏好英文详描；中英接入皆不出错，领导提示绵绵不绝ಲ：风格语义拼装
    workflow["4"]["inputs"]["text"] = f"{prompt}. Style: {style}. {_PROMPT_HINT}"
    workflow["6"]["inputs"]["width"] = width
    workflow["6"]["inputs"]["height"] = height
    workflow["6"]["inputs"]["length"] = frames
    workflow["7"]["inputs"]["noise_seed"] = random.randrange(1, 2**31)
    workflow["13"]["inputs"]["fps"] = float(_FPS)
    workflow["14"]["inputs"]["filename_prefix"] = f"neyria/{uuid.uuid4().hex[:10]}"
    return workflow


def _submit(workflow: dict[str, Any]) -> str:
    response = httpx.post(
        f"{config.COMFYUI_BASE_URL}/prompt",
        json={"prompt": workflow, "client_id": f"neyria-{uuid.uuid4().hex[:8]}"},
        timeout=30.0,
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
        response = httpx.get(url, timeout=30.0)
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
    response = httpx.get(f"{config.COMFYUI_BASE_URL}/view", params=params, timeout=300.0, follow_redirects=True)
    response.raise_for_status()
    return response.content


def generate_video(inputs: VideoGenerateInput) -> dict[str, Any]:
    """真实出视频：本地 GPU 生成 → 轮询到终态 → 拉回 mp4 → 本地资产落库。"""
    if not config.COMFYUI_BASE_URL:
        raise ModelNotConfiguredError("视频模型尚未配置")

    workflow = _load_workflow(inputs.prompt, inputs.style, inputs.ratio, inputs.duration)
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
