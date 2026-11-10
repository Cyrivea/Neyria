"""受控生产能力 API。"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from core.deps import verify_token
from core.ratelimit import capability_rate_limit
from schemas.capability import CapabilityResult, CapabilityRunRequest
from services.billing_service import DuplicateReservationError
from services.capabilities import CapabilityContext, capability_features, run_capability
from services.project_service import get_project

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("/{capability_id}/features")
def capability_features_route(capability_id: str, user=Depends(verify_token)):
    """面板用的能力描述：当前模型能不能吃参考图、最多几张。

    与 Provider 实现同步（IMAGE_REFERENCE_SUPPORTED），用户不需要传完才发现不支持。
    """
    features = capability_features(capability_id)
    if features is None:
        raise HTTPException(status_code=404, detail="未注册的生产能力")
    return features


@router.post("/run", response_model=CapabilityResult)
async def run_capability_route(req: CapabilityRunRequest, user=Depends(capability_rate_limit)):
    if req.project_id is not None and await asyncio.to_thread(get_project, user["id"], req.project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        return await run_capability(
            req.capability_id,
            req.inputs.model_dump(),
            context=CapabilityContext(
                user_id=user["id"],
                project_id=req.project_id,
                source="standalone",
            ),
            idempotency_key=req.idempotency_key,
        )
    except DuplicateReservationError:
        raise HTTPException(
            status_code=409,
            detail="相同请求正在处理中或已完成，请勿重复提交",
        ) from None
