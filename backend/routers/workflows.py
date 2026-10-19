"""Workflow Draft 与运行 API。"""

import asyncio

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from core.deps import verify_token
from schemas.workflow import WorkflowDraftRequest, WorkflowDraftResponse, WorkflowRunResponse
from services.project_service import get_project
from services.workflow_service import (
    delete_run,
    get_draft,
    get_run_preview,
    get_run_status_detail,
    list_runs,
    save_draft,
    set_run_kept,
    submit_workflow_run,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])


def _check_project(user_id: int, project_id: int | None) -> None:
    if project_id is not None and get_project(user_id, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")


@router.get("/draft", response_model=WorkflowDraftResponse)
def get_workflow_draft(project_id: int | None = Query(None), user=Depends(verify_token)):
    _check_project(user["id"], project_id)
    return get_draft(user["id"], project_id)


@router.put("/draft", response_model=WorkflowDraftResponse)
def save_workflow_draft(req: WorkflowDraftRequest, user=Depends(verify_token)):
    _check_project(user["id"], req.project_id)
    return save_draft(user["id"], req.project_id, req.draft, req.expected_revision)


@router.get("/runs/latest")
def latest_run_status(project_id: int | None = Query(None), user=Depends(verify_token)):
    """当前项目最近一次 Workflow 运行的状态与各节点步骤（前端进度面板/排查用）。"""
    _check_project(user["id"], project_id)
    return get_run_status_detail(user["id"], project_id, None)


@router.get("/runs/{run_id}")
def get_run_status_route(run_id: str, user=Depends(verify_token)):
    return get_run_status_detail(user["id"], None, run_id)


@router.post("/run", response_model=WorkflowRunResponse, status_code=202)
async def run_workflow(project_id: int | None = Query(None), user=Depends(verify_token)):
    await asyncio.to_thread(_check_project, user["id"], project_id)
    return await asyncio.to_thread(submit_workflow_run, user["id"], project_id)


@router.get("/run-preview")
def run_preview(project_id: int | None = Query(None), user=Depends(verify_token)):
    """WF1-③ 全局运行前报价：链 × 块级费用预估 + 缓存命中/将重跑标记。

    判定逻辑与真实执行共享同一套 _resolve_params/_params_fingerprint，且按
    capability 预占价目（计费模式本身开不开都报同等额度——预估值与用户账单一头消）。
    """
    _check_project(user["id"], project_id)
    return get_run_preview(user["id"], project_id)


@router.get("/runs")
def list_workflow_runs(
    project_id: int | None = Query(None),
    limit: int = Query(20, ge=1, le=20),
    user=Depends(verify_token),
):
    """队列档案馆：本项目的运行历史（新→旧），帯按链分组的产物摘要。"""
    _check_project(user["id"], project_id)
    return list_runs(user["id"], project_id, limit)


@router.patch("/runs/{run_id}/keep")
def keep_workflow_run(
    run_id: str, kept: bool = Body(embed=True), user=Depends(verify_token)
):
    """「满意才保留」旗标；未来自动清理策略豁免名单的唯一入口。"""
    _check_project(user["id"], None)
    return set_run_kept(user["id"], run_id, kept)


@router.delete("/runs/{run_id}")
def delete_workflow_run(run_id: str, user=Depends(verify_token)):
    """「不满意删除」：删记录 + 删该次运行产物文件，积分不退还（诚实字段 refund=false）。"""
    _check_project(user["id"], None)
    return delete_run(user["id"], run_id)
