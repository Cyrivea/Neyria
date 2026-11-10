"""本地 ComfyUI 图像 Provider（Illustrious-XL）：模板打参、尺寸映射、下载转存、离线语义。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from core import config
from schemas.capability import ImageGenerateInput
from services.capabilities import comfyui_image, runtime
from services.capabilities.zhipu_text import ModelNotConfiguredError

PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture(autouse=True)
def _no_translation(monkeypatch):
    """翻译要打真 LLM；这里锁成原样返回，单测只关心工作流本身。"""
    monkeypatch.setattr(
        comfyui_image, "_translate_to_tags", lambda inputs: f"{inputs.prompt}, {inputs.style}"
    )


def _template() -> dict:
    return json.loads(Path(comfyui_image._TEMPLATE_PATH).read_text(encoding="utf-8"))


# ---------- 模板打参 ----------

def test_workflow_maps_ratio_to_sdxl_bucket_and_random_seed(monkeypatch):
    monkeypatch.setattr(config, "IMAGE_LORA_COMFYUI", "ILwatercolor.safetensors")
    workflow = comfyui_image._load_workflow(
        ImageGenerateInput(prompt="雨夜小店", style="水彩", ratio="9:16")
    )
    assert workflow["4"]["inputs"]["width"] == 768
    assert workflow["4"]["inputs"]["height"] == 1344
    assert "雨夜小店" in workflow["2"]["inputs"]["text"]
    assert "masterpiece" in workflow["2"]["inputs"]["text"]
    assert workflow["5"]["inputs"]["seed"] != 1234567890  # 模板种子被打散
    assert workflow["10"]["inputs"]["lora_name"] == "ILwatercolor.safetensors"


def test_workflow_bypasses_lora_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "IMAGE_LORA_COMFYUI", "")
    workflow = comfyui_image._load_workflow(ImageGenerateInput(prompt="cat", style="watercolor"))
    assert "10" not in workflow
    assert workflow["2"]["inputs"]["clip"] == ["1", 1]
    assert workflow["5"]["inputs"]["model"] == ["1", 0]


def test_workflow_keeps_template_readable():
    """模板必须一直能被 json 解析（导入失败会让整个能力 500）。"""
    assert set(_template()) >= {"1", "2", "3", "4", "5", "6", "7"}


# ---------- 中文 → tag：失败降级，不拖垮生成 ----------

def test_translate_falls_back_when_llm_unavailable(monkeypatch):
    monkeypatch.setattr(config, "COMFYUI_IMAGE_TRANSLATE_PROMPT", True)
    monkeypatch.setattr("services.llm.factory.get_chat_client", lambda: None)
    out = comfyui_image._translate_to_tags(
        ImageGenerateInput(prompt="雨夜霓虹小店", style="二次元", palette="深蓝")
    )
    assert "雨夜霓虹小店" in out  # 透传而不是丢失


def test_translate_skips_llm_for_ascii_prompt(monkeypatch):
    monkeypatch.setattr(config, "COMFYUI_IMAGE_TRANSLATE_PROMPT", True)

    def _boom():
        raise AssertionError("纯英文提示词不该调用 LLM")

    monkeypatch.setattr("services.llm.factory.get_chat_client", _boom)
    out = comfyui_image._translate_to_tags(ImageGenerateInput(prompt="rainy neon shop", style="anime"))
    assert "rainy neon shop" in out


# ---------- 提交 → 轮询 → 下载 ----------

class _FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.content = content

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=httpx.Request("GET", "http://x"), response=None)


def test_generate_image_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "COMFYUI_BASE_URL", "http://127.0.0.1:8188")
    monkeypatch.setattr(config, "IMAGE_LORA_COMFYUI", "ILwatercolor.safetensors")
    monkeypatch.setattr(config, "DOCUMENT_STORAGE_DIR", tmp_path / "documents")

    calls: list[str] = []

    def _post(url, **kwargs):
        calls.append(url)
        return _FakeResponse(payload={"prompt_id": "pid-1"})

    def _get(url, **kwargs):
        calls.append(url)
        if "/history/" in url:
            return _FakeResponse(
                payload={
                    "pid-1": {
                        "status": {"completed": True},
                        "outputs": {
                            "7": {
                                "images": [
                                    {"filename": "neyria_image_00001_.png", "subfolder": "neyria", "type": "output"}
                                ]
                            }
                        },
                    }
                }
            )
        return _FakeResponse(content=PNG_HEADER)

    monkeypatch.setattr(comfyui_image.httpx, "post", _post)
    monkeypatch.setattr(comfyui_image.httpx, "get", _get)

    result = comfyui_image.generate_image(
        ImageGenerateInput(prompt="a cat", style="watercolor", ratio="16:9")
    )

    assert result["content"].startswith("![曲绘](/api/assets/")
    asset = result["asset"]
    assert asset["capability_id"] == "image.generate"
    assert (tmp_path / "documents").parent.joinpath("assets", asset["filename"]).read_bytes() == PNG_HEADER
    assert result["usage"]["model"] == config.IMAGE_MODEL_COMFYUI
    assert any("/prompt" in c for c in calls)


def test_generate_image_reports_comfyui_offline(monkeypatch):
    monkeypatch.setattr(config, "COMFYUI_BASE_URL", "http://127.0.0.1:8188")

    def _post(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(comfyui_image.httpx, "post", _post)
    with pytest.raises(ModelNotConfiguredError):
        comfyui_image.generate_image(ImageGenerateInput(prompt="a cat", style="anime"))


def test_generate_image_surfaces_comfyui_execution_error(monkeypatch):
    monkeypatch.setattr(config, "COMFYUI_BASE_URL", "http://127.0.0.1:8188")
    monkeypatch.setattr(comfyui_image.httpx, "post", lambda url, **kwargs: _FakeResponse(payload={"prompt_id": "pid-2"}))
    monkeypatch.setattr(
        comfyui_image.httpx,
        "get",
        lambda url, **kwargs: _FakeResponse(
            payload={
                "pid-2": {
                    "status": {
                        "status_str": "error",
                        "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]],
                    }
                }
            }
        ),
    )
    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        comfyui_image.generate_image(ImageGenerateInput(prompt="a cat", style="anime"))


# ---------- Runtime 分流与面板能力描述 ----------

def test_runtime_routes_image_to_comfyui(monkeypatch):
    monkeypatch.setattr(config, "IMAGE_PROVIDER", "comfyui")
    sent: list[ImageGenerateInput] = []
    monkeypatch.setattr(
        comfyui_image, "generate_image", lambda inputs: sent.append(inputs) or {"content": "ok"}
    )
    out = runtime._image_executor(ImageGenerateInput(prompt="a cat", style="anime"))
    assert out == {"content": "ok"}
    assert sent and sent[0].prompt == "a cat"


def test_features_report_provider_ratio_sizes(monkeypatch):
    monkeypatch.setattr(config, "IMAGE_PROVIDER", "comfyui")
    features = runtime.capability_features("image.generate")
    assert features is not None
    assert features["ratio_sizes"]["16:9"] == "1344×768"
    monkeypatch.setattr(config, "IMAGE_PROVIDER", "kunbot")
    features = runtime.capability_features("image.generate")
    assert features is not None
    assert features["ratio_sizes"]["16:9"] == "1536×1024"
