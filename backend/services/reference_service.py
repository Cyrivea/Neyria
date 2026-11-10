"""参考图（图生图的输入素材）落盘与清理。

为什么走 assets 表而不是另建一张表：`runtime._normalize_source_asset` 的属主校验
只认 assets 行（kind='image'），参考图必须借用同一条安全边界，否则会出现
「A 打开 B 的参考图地址就能拿去生成」的越权面。

为什么 run_id 留空：档案馆按 run_id 分组列出产物，参考图不是产出；
capability_id 记 'image.reference'，与生成产物（'image.generate'）区分得开。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from core import config
from repositories import asset_repo

MAX_REFERENCE_BYTES = 10 * 1024 * 1024  # 单张参考图 10MB
_ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}


def assets_dir() -> Path:
    out = Path(config.DOCUMENT_STORAGE_DIR).parent / "assets"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _sniff_image(blob: bytes) -> tuple[str, str] | None:
    """按魔数判定真实格式（不信任客户端给的 MIME 与扩展名）。"""
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if blob.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if blob.startswith(b"RIFF") and blob[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


def store_reference_image(
    blob: bytes, declared_type: str | None, user_id: int, project_id: int | None
) -> dict[str, object]:
    """落盘 + 登记资产行，返回前端需要的定位信息。

    校验顺序：大小 → 魔数 → 落盘 → 登记。任何一步失败都不留下半成品文件。
    """
    if not blob:
        raise ValueError("参考图为空")
    if len(blob) > MAX_REFERENCE_BYTES:
        raise ValueError(f"参考图不能超过 {MAX_REFERENCE_BYTES // (1024 * 1024)}MB")
    sniffed = _sniff_image(blob)
    if sniffed is None:
        raise ValueError("只支持 PNG / JPEG / WEBP 参考图")
    if declared_type and declared_type not in _ALLOWED_CONTENT_TYPES:
        # 客户端声明与魔数不一致时以魔数为准，但明显不是图片的直接拒
        raise ValueError("只支持 PNG / JPEG / WEBP 参考图")
    suffix, content_type = sniffed

    filename = f"{uuid.uuid4().hex}{suffix}"
    path = assets_dir() / filename
    path.write_bytes(blob)
    try:
        row = asset_repo.insert(
            user_id=user_id,
            project_id=project_id,
            run_id=None,
            capability_id="image.reference",
            kind="image",
            filename=filename,
            content_type=content_type,
            byte_size=len(blob),
            prompt_snapshot=None,
            usage_event_id=None,
            created_at=datetime.now(UTC).isoformat(),
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise

    return {
        "asset_id": row["id"],
        "filename": filename,
        "path": f"/api/assets/{filename}",
        "content_type": content_type,
        "byte_size": len(blob),
    }


def delete_reference_image(asset_id: str, user_id: int) -> bool:
    """删除自己名下的参考图（文件 + 资产行）。越权与不存在同口径返回 False。"""
    row = asset_repo.get_owned(asset_id, user_id)
    if row is None or row.get("capability_id") != "image.reference":
        return False
    asset_repo.delete_by_filename(user_id, row["filename"])
    try:
        (assets_dir() / row["filename"]).unlink(missing_ok=True)
    except OSError:
        # 文件已不在磁盘上也算删除成功：资产行才是事实来源
        return True
    return True
