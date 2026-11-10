"""参考图链路（2026-11-09 选 A「接进能力」）：schema 归一、属主校验、预检拒绝、上传/删除。

这里锁的是用户能感知的语义：
1. 参考图必须是**真上传**的资产（assets 行），而不是 localStorage 里的文件名；
2. 当前模型不支持图生图时，必须在**预占积分之前**如实拒绝——不静默忽略、不烧钱；
3. 别人的参考图不能拿来生成（越权与不存在同口径）。
"""

import base64

import pytest
from pydantic import ValidationError

from core import config
from core.database import db_connection
from schemas.capability import ImageGenerateInput
from services.auth_service import hash_password
from services.capabilities import CapabilityContext, capability_features, run_capability

# 1x1 透明 PNG
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _user_id(username: str) -> int:
    with db_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, profile, created_at) "
            "VALUES (?, ?, ?, ?)",
            (username, hash_password("unused-password"), "", "2026-01-01"),
        )
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    return int(row[0])


# ---------- schema：草稿里的扁平字符串要能归一成列表 ----------

def test_reference_assets_accepts_flat_draft_string():
    model = ImageGenerateInput(prompt="雨夜小店", reference_assets="asset-abc123,/api/assets/x.jpg")
    assert model.reference_assets == ["asset-abc123", "/api/assets/x.jpg"]


def test_reference_assets_rejects_more_than_three():
    with pytest.raises(ValidationError):
        ImageGenerateInput(prompt="x", reference_assets=["a1", "b2", "c3", "d4"])


def test_reference_assets_empty_string_means_no_reference():
    assert ImageGenerateInput(prompt="x", reference_assets="").reference_assets == []


# ---------- 能力描述：面板据此提前告知「当前模型吃不吃参考图」 ----------

def test_features_exposes_reference_support():
    features = capability_features("image.generate")
    assert features is not None
    assert features["reference_limit"] == 3
    assert features["reference_supported"] == config.IMAGE_REFERENCE_SUPPORTED
    assert capability_features("nope.generate") is None


# ---------- 预检：不支持时在预占之前拒绝，且不触达 Provider ----------

async def test_preflight_rejects_references_when_provider_cannot_use_them(monkeypatch):
    user_id = _user_id("ref-preflight@local.test")
    from services.reference_service import store_reference_image

    asset = store_reference_image(PNG_BYTES, "image/png", user_id, None)

    monkeypatch.setattr(config, "IMAGE_REFERENCE_SUPPORTED", False)

    def _must_not_be_called(_inputs):
        raise AssertionError("预检拒绝后不得进入 Provider 执行器")

    from services.capabilities import runtime

    monkeypatch.setattr(runtime, "_image_executor", _must_not_be_called)

    result = await run_capability(
        "image.generate",
        {"prompt": "雨夜小店", "reference_assets": [asset["asset_id"]]},
        context=CapabilityContext(user_id=user_id, project_id=None, source="workflow"),
    )
    assert result.status == "failed"
    assert "不支持参考图" in (result.error or "")


async def test_preflight_rejects_someone_elses_reference(monkeypatch):
    owner_id = _user_id("ref-owner@local.test")
    intruder_id = _user_id("ref-intruder@local.test")
    from services.reference_service import store_reference_image

    asset = store_reference_image(PNG_BYTES, "image/png", owner_id, None)

    result = await run_capability(
        "image.generate",
        {"prompt": "雨夜小店", "reference_assets": [asset["asset_id"]]},
        context=CapabilityContext(user_id=intruder_id, project_id=None, source="workflow"),
    )
    assert result.status == "failed"
    assert "不存在" in (result.error or "")
