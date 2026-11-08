from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from core.deps import verify_token
from core.ratelimit import chat_rate_limit, enforce_upload_limits
from repositories.chat_repo import set_feedback
from schemas.chat import ChatRequest, FeedbackRequest, ProfileRequest, RagDocumentDeleteRequest
from services.assistant_service import (
    clear_history,
    get_history,
    get_rag_documents,
    load_profile,
    rebuild_rag_document,
    remove_rag_document,
    save_profile,
    upload_document,
)
from services.chat.chat_orchestrator import stream_chat

router = APIRouter(tags=["assistant"])


@router.post("/chat")
async def chat(req: ChatRequest, user=Depends(chat_rate_limit)):
    # 输入语义校验必须在 StreamingResponse 之前：响应头一旦发出，生成器里再抛
    # HTTPException 只能变成 500 断流（A1 接线测试真机抓到，服务层同名检查保留
    # 作双保险——两个错误来源不同：路由层防接线、服务层防内部调用）。
    if req.regenerate and req.edit_from_id is not None:
        raise HTTPException(status_code=400, detail="regenerate 与 edit_from_id 互斥，只能传一个")
    return StreamingResponse(
        stream_chat(
            user,
            req.message,
            req.project_id,
            [item.model_dump() for item in req.attachments],
            regenerate=req.regenerate,
            edit_from_id=req.edit_from_id,
        ),
        media_type="text/event-stream",
    )


@router.post("/profile")
def set_profile(req: ProfileRequest, user=Depends(verify_token)):
    save_profile(user["id"], req.profile)
    return {"status": "success", "message": "个人信息已更新"}


@router.get("/profile")
def get_profile_route(user=Depends(verify_token)):
    return {"profile": load_profile(user["id"])}




@router.post("/feedback")
def set_message_feedback(req: FeedbackRequest, user=Depends(verify_token)):
    """UI2-⑤：有用/没用评分。value=0 视同清除（落 NULL，保持列语义三态）。"""
    if req.message_id <= 0:
        raise HTTPException(status_code=422, detail="message_id 必须为正整数")
    ok = set_feedback(user["id"], req.message_id, req.value if req.value != 0 else None)
    if not ok:
        raise HTTPException(status_code=404, detail="消息不存在或不属于当前账号")
    return {"status": "success"}


@router.get("/history")
def get_history_route(project_id: int | None = None, user=Depends(verify_token)):
    return get_history(user["id"], project_id)


@router.delete("/history")
def clear_history_route(project_id: int | None = None, user=Depends(verify_token)):
    return clear_history(user["id"], project_id)


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_file(
    file: UploadFile = File(...),
    project_id: int | None = Query(None),
    user=Depends(verify_token),
):
    # UploadFile 底层是 SpooledTemporaryFile（超阈值自动落盘），seek 到末尾即可拿到大小而不读入内存
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    await enforce_upload_limits(user["id"], size)
    return await upload_document(file, user["id"], project_id, size)


@router.get("/documents")
def list_rag_documents_route(project_id: int | None = None, user=Depends(verify_token)):
    return get_rag_documents(user["id"], project_id)


@router.delete("/documents")
def delete_rag_document_route(req: RagDocumentDeleteRequest, user=Depends(verify_token)):
    return remove_rag_document(req.filename, user["id"], req.project_id, req.document_id)


@router.post("/documents/reindex")
def reindex_rag_document_route(
    document_id: str | None = Query(None, min_length=32, max_length=32),
    filename: str | None = Query(None, min_length=1, max_length=255),
    project_id: int | None = Query(None),
    user=Depends(verify_token),
):
    if document_id is None and filename is None:
        raise HTTPException(status_code=422, detail="document_id 或 filename 至少提供一个")
    return rebuild_rag_document(
        document_id=document_id, filename=filename, user_id=user["id"], project_id=project_id
    )
