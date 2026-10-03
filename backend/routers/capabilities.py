"""受控生产能力 API。"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from core.ratelimit import capability_rate_limit
from schemas.capability import CapabilityResult, CapabilityRunRequest
from services.billing_service import DuplicateReservationError
from services.capabilities import CapabilityContext, run_capability
from services.project_service import get_project

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


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
