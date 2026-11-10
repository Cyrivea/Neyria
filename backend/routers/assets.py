"""生成产物静态服务：/assets/{uuid}.png。

- 文件名只放行 uuid-hex + 白名单扩展名，路径穿越进不来。
- 不加鉴权：文件名本身是不可预测的票据（32 位 hex）。前端 <img> 不便携带
  Authorization 头；内容只是生成图，不牵用户私有数据。后续若放敏感素材，
  可改 token 签名 URL（HMAC）再升级。
- 参考图上传/删除是需要鉴权的写入面（POST /assets/reference），不加到静态 GET 上。
"""

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from core import config
from core.deps import verify_token
from core.ratelimit import enforce_upload_limits
from services.reference_service import (
    MAX_REFERENCE_BYTES,
    delete_reference_image,
    store_reference_image,
)

router = APIRouter(prefix="/assets", tags=["assets"])

_ALLOWED_SUFFIX = {".png", ".jpg", ".jpeg", ".webp", ".mp4"}


@router.post("/reference", status_code=201)
async def upload_reference(
    file: UploadFile = File(...),
    project_id: int | None = Query(None),
    user=Depends(verify_token),
):
    """参考图上传（图生图的输入素材）。

    限制跟普通上传同一套配额（每日字节总量），单张上限更严（10MB）——
    参考图是给模型做输入用的，不是作品存储。
    """
    blob = await file.read()
    if len(blob) > MAX_REFERENCE_BYTES:
        raise HTTPException(
            status_code=413, detail=f"参考图不能超过 {MAX_REFERENCE_BYTES // (1024 * 1024)}MB"
        )
    await enforce_upload_limits(user["id"], len(blob))
    try:
        return store_reference_image(blob, file.content_type, user["id"], project_id)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from None


@router.delete("/reference/{asset_id}")
def delete_reference(asset_id: str, user=Depends(verify_token)):
    if not delete_reference_image(asset_id, user["id"]):
        raise HTTPException(status_code=404, detail="参考图不存在或不属于当前账号")
    return {"status": "success"}


@router.get("/{filename}")
def get_asset(filename: str):
    name = Path(filename).name
    if Path(filename).name != filename or Path(name).suffix not in _ALLOWED_SUFFIX:
        raise HTTPException(status_code=404, detail="资产不存在")
    path = Path(config.DOCUMENT_STORAGE_DIR).parent / "assets" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="资产不存在")
    return FileResponse(path)
