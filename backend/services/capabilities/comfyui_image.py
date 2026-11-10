"""ComfyUI 本地图像 Provider（Illustrious-XL / SDXL 动漫底模，自建算力）。

与 `comfyui_video.py` 同构的三段式：POST /prompt 提交 API 格式工作流 → 轮询
/history 到终态 → /view 拉回 PNG 转存到本地 assets。模板见
`templates/illustrious_t2i.json`（节点 2=正向、3=负向、4=宽高、5=种子）。

两个本地特有的坑（真机 2026-11-09）：
1. **Illustrious 的 CLIP 只吃英文 tag**：中文描述直接塞进去，模型会忘掉画面内容
   只画「一个好看的二次元女孩」（实测：写「雨夜霓虹小店」出的是通用角色图）。
   所以中文 → 英文 Danbooru 风 tag 先过一遍平台 LLM；拿不到 LLM 时如实降级透传，
   并在日志里留痕，不假装翻译过。
2. **WSL → Windows 宿主**：ComfyUI 必须 `--listen 0.0.0.0`，COMFYUI_BASE_URL 填宿主 IP；
   `trust_env=False` 防止环境代理把内网请求推给 7897 代理。

账务：本地算力上游成本按 0 记（provider_cost 表显式 0），但 usage 照记，模型名
`Illustrious-XL-v1.1/local`。调用方负责丢线程池（同步 httpx + 阻塞轮询）。
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from core import config
from schemas.capability import ImageGenerateInput
from services.capabilities.zhipu_text import ModelNotConfiguredError

logger = logging.getLogger("capabilities.comfyui_image")

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "illustrious_t2i.json"

# SDXL 官方推荐分桶（均为 8 的倍数、约 1MP）；4:3 落在 1152×864
_RATIO_TO_SIZE: dict[str, tuple[int, int]] = {
    "16:9": (1344, 768),
    "1:1": (1024, 1024),
    "9:16": (768, 1344),
    "4:3": (1152, 864),
}
_POLL_INTERVAL_SECONDS = 1.5
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 平台 LLM 只做一件事：把中文画面描述压成 SDXL 能读的英文 tag 串。
_TRANSLATE_SYSTEM = (
    "You convert Chinese image descriptions into English Danbooru-style tags for an SDXL anime model. "
    "Output ONLY a comma-separated tag list: subject, count, appearance, outfit, pose, scene, props, "
    "lighting, camera, style. No explanations, no quotes, no markdown, at most 45 tags."
)


def _assets_dir() -> Path:
    out = Path(config.DOCUMENT_STORAGE_DIR).parent / "assets"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _translate_to_tags(inputs: ImageGenerateInput) -> str:
    """中文/混合描述 → 英文 tag；纯英文原样返回。任何失败都降级为原文本。"""
    raw = f"{inputs.prompt}. Style: {inputs.style}. Palette: {inputs.palette}".strip()
    if not config.COMFYUI_IMAGE_TRANSLATE_PROMPT or not _CJK_RE.search(raw):
        return raw
    try:
        from services.llm.factory import get_chat_client

        client = get_chat_client()
        if client is None:
            raise RuntimeError("LLM 客户端不可用")
        reply = client.complete(
            model=config.CHAT_MODEL,
            messages=[
                {"role": "system", "content": _TRANSLATE_SYSTEM},
                {"role": "user", "content": raw},
            ],
            # 网关当前兜底到推理型模型（DeepSeek-V4.1）：思考 token 会先吃掉预算，
            # 220 时 content 恒为空（实测 reasoning_tokens=220/220）——给足 1200 才能拿到答案。
            max_tokens=1200,
        )
        text = re.sub(r"[\r\n]+", " ", (reply.content or "").strip())
        text = text.strip(" \"'`")
        if not text:
            raise RuntimeError("翻译结果为空")
        logger.info(
            "中文提示词已转英文 tag",
            extra={"evt": "comfyui_image_prompt_translated", "chars": len(text)},
        )
        return text
    except Exception as exc:
        logger.warning(
            "提示词翻译失败，按原文透传（本地模型对中文理解很弱）",
            extra={"evt": "comfyui_image_translate_failed", "error_type": type(exc).__name__},
        )
        return raw


def _load_workflow(inputs: ImageGenerateInput) -> dict[str, Any]:
    workflow: dict[str, Any] = json.loads(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    tags = _translate_to_tags(inputs)
    workflow["2"]["inputs"]["text"] = (
        "masterpiece, best quality, amazing quality, very aesthetic, absurdres, " + tags
    )
    # SDXL 动漫底模有强人物偏置：翻译结果明确是「纯场景」时，把人物写进负面词，
    # 否则会画出「没人要的 1girl」（真机实测：no humans/scenery 仍出人物）。
    if "no humans" in tags.lower() or "scenery" in tags.lower():
        workflow["3"]["inputs"]["text"] += ", 1girl, 1boy, person, people, solo"
    width, height = _RATIO_TO_SIZE[inputs.ratio]
    workflow["4"]["inputs"]["width"] = width
    workflow["4"]["inputs"]["height"] = height
    workflow["5"]["inputs"]["seed"] = random.randrange(1, 2**31)
    if config.IMAGE_LORA_COMFYUI:
        workflow["10"]["inputs"]["lora_name"] = config.IMAGE_LORA_COMFYUI
        workflow["10"]["inputs"]["strength_model"] = config.IMAGE_LORA_STRENGTH_COMFYUI
        workflow["10"]["inputs"]["strength_clip"] = config.IMAGE_LORA_STRENGTH_COMFYUI
    else:
        # 留空 = 不要 LoRA：旁路掉节点 10，把 CLIP/MODEL 直接接到底模输出
        workflow["2"]["inputs"]["clip"] = ["1", 1]
        workflow["3"]["inputs"]["clip"] = ["1", 1]
        workflow["5"]["inputs"]["model"] = ["1", 0]
        workflow.pop("10", None)
    return workflow


def _submit(workflow: dict[str, Any]) -> str:
    try:
        response = httpx.post(
            f"{config.COMFYUI_BASE_URL}/prompt",
            json={"prompt": workflow, "client_id": f"neyria-image-{uuid.uuid4().hex[:8]}"},
            timeout=30.0,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        raise ModelNotConfiguredError("本地图像模型未启动（ComfyUI 不可达）") from exc
    response.raise_for_status()
    prompt_id = response.json().get("prompt_id")
    if not prompt_id:
        raise RuntimeError("ComfyUI 未返回 prompt_id")
    return str(prompt_id)


def _pick_image_file(outputs: dict[str, Any]) -> dict[str, str]:
    for node_out in outputs.values():
        for item in node_out.get("images") or []:
            filename = str(item.get("filename", ""))
            if filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                return {
                    "filename": filename,
                    "subfolder": str(item.get("subfolder", "")),
                    "type": str(item.get("type", "output")),
                }
    raise RuntimeError("ComfyUI 输出里没有找到图片文件")


def _wait_for_image(prompt_id: str) -> dict[str, str]:
    deadline = time.monotonic() + config.COMFYUI_TIMEOUT_SECONDS
    url = f"{config.COMFYUI_BASE_URL}/history/{prompt_id}"
    while time.monotonic() < deadline:
        response = httpx.get(url, timeout=30.0, trust_env=False)
        if response.status_code == 200:
            entry = response.json().get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                if status.get("completed"):
                    return _pick_image_file(entry.get("outputs") or {})
                if status.get("status_str") == "error":
                    messages = [
                        m[1].get("exception_message", "")
                        for m in status.get("messages", [])
                        if isinstance(m, list) and len(m) > 1 and isinstance(m[1], dict)
                    ]
                    detail = "; ".join(t for t in messages if t)
                    raise RuntimeError(f"ComfyUI 生成失败（{detail or '未知原因'}）")
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"ComfyUI 生成超时（{config.COMFYUI_TIMEOUT_SECONDS}s），请稍后重试")


def _download(file_ref: dict[str, str]) -> bytes:
    response = httpx.get(
        f"{config.COMFYUI_BASE_URL}/view",
        params={
            "filename": file_ref["filename"],
            "subfolder": file_ref["subfolder"],
            "type": file_ref["type"],
        },
        timeout=300.0,
        follow_redirects=True,
        trust_env=False,
    )
    response.raise_for_status()
    return response.content


def generate_image(inputs: ImageGenerateInput) -> dict[str, Any]:
    """真实出图：本地 GPU 生成 → 拉回 PNG → 本地资产落库。

    ComfyUI 不在线时抛 ModelNotConfiguredError（Runtime 会转成 model_unavailable），
    与视频 Provider 同一语义：本地算力离线要说「模型未启动」，不说「生成失败」。
    """
    if not config.COMFYUI_BASE_URL:
        raise ModelNotConfiguredError("图像模型尚未配置")
    workflow = _load_workflow(inputs)
    prompt_id = _submit(workflow)
    file_ref = _wait_for_image(prompt_id)
    blob = _download(file_ref)

    filename = f"{uuid.uuid4().hex}.png"
    path = _assets_dir() / filename
    path.write_bytes(blob)

    asset_row = {
        "asset_id": f"asset-{uuid.uuid4().hex[:12]}",
        "filename": filename,
        "path": f"/api/assets/{filename}",
        "capability_id": "image.generate",
        "prompt_snapshot": inputs.model_dump(),
        "byte_size": len(blob),
    }

    return {
        "content": f"![曲绘](/api/assets/{filename})",
        "asset_path": f"/api/assets/{filename}",
        "asset": asset_row,
        "usage": {
            "model": config.IMAGE_MODEL_COMFYUI,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
