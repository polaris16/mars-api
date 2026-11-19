"""记忆会话、素材上传与片段附件 API。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.db.database import get_db
from app.schemas.memory import (
    AttachmentBatchCreate, AttachmentPatch, AttachmentResponse, DeleteResponse,
    FragmentCreate, FragmentCreatedResponse, FragmentPage, FragmentPatch, FragmentResponse, MediaAssetPage,
    MediaAssetResponse, SessionCreate, SessionDetail, SessionPage, SessionPatch,
    SessionSummary, UploadAuthorizationRequest, UploadAuthorizationResponse,
    UploadCompleteRequest,
)
from app.schemas.memory_extraction import ExtractionRequest
from app.services.memory_extraction_service import MemoryExtractionService, get_memory_extraction_service
from app.services.object_storage_service import ObjectStorageService, get_object_storage_service
from app.services.memory_service import MemoryService

router = APIRouter(tags=["recordings and media"])


# [新增] 本地 Mock 存储签发的短时上传/下载 URL；由 token 授权具体对象操作。
@router.put("/mock-storage/upload/{token}", include_in_schema=False)
async def mock_storage_upload(token: str, request: Request, storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)]):
    await storage.receive_mock_upload(token, request)
    return {"uploaded": True}


@router.get("/mock-storage/download/{token}", include_in_schema=False)
def mock_storage_download(token: str, storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)]):
    path, content_type = storage.mock_download(token)
    return FileResponse(path, media_type=content_type, headers={"Content-Disposition": "inline"})


@router.post("/recording-sessions", response_model=SessionSummary, status_code=status.HTTP_201_CREATED, summary="创建录音会话")
def create_session(user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)], body: SessionCreate):
    return service.create_session(db, user, body)


@router.get("/recording-sessions", response_model=SessionPage, summary="分页查询录音会话")
def list_sessions(user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)], page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 20, sort: Literal["last_recorded_at", "created_at"] = "last_recorded_at", order: Literal["asc", "desc"] = "desc", is_favorite: bool | None = None):
    return service.list_sessions(db, user, page, page_size, sort, order, is_favorite)


@router.get("/recording-sessions/{session_id}", response_model=SessionDetail, summary="获取录音会话详情")
def get_session(session_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)], storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)]):
    return service.get_session_detail(db, user, session_id, storage)


@router.patch("/recording-sessions/{session_id}", response_model=SessionSummary, summary="更新录音会话")
def patch_session(session_id: int, body: SessionPatch, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)]):
    return service.patch_session(db, user, session_id, body)


@router.delete("/recording-sessions/{session_id}", response_model=DeleteResponse, summary="删除录音会话")
def delete_session(session_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)]):
    service.delete_session(db, user, session_id)
    return {"id": session_id, "deleted": True}


@router.post("/media-assets/upload-authorizations", response_model=UploadAuthorizationResponse, status_code=status.HTTP_201_CREATED, summary="申请媒体上传授权")
def upload_authorization(body: UploadAuthorizationRequest, response: Response, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)], storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)]):
    result = service.create_upload_authorization(db, user, body, storage)
    if result["reused"]:
        response.status_code = status.HTTP_200_OK
    return result


@router.post("/media-assets/{asset_id}/complete", response_model=MediaAssetResponse, summary="确认媒体上传完成")
def complete_upload(asset_id: int, body: UploadCompleteRequest, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)], storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)]):
    asset = service.complete_upload(db, user, asset_id, body, storage)
    return service.get_asset(db, user, asset.id, storage)


@router.get("/media-assets", response_model=MediaAssetPage, summary="分页查询素材库")
def list_assets(user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)], storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)], page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 20, purpose: Literal["recording", "source", "attachment"] | None = None, media_type: Literal["audio", "image", "video", "text"] | None = None):
    return service.list_assets(db, user, page, page_size, purpose, media_type, storage)


@router.get("/media-assets/{asset_id}", response_model=MediaAssetResponse, summary="获取素材详情和临时读取地址")
def get_asset(asset_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)], storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)]):
    return service.get_asset(db, user, asset_id, storage)


@router.delete("/media-assets/{asset_id}", response_model=DeleteResponse, summary="删除素材")
def delete_asset(asset_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)]):
    service.delete_asset(db, user, asset_id)
    return {"id": asset_id, "deleted": True}


# [删除-旧] 片段创建只保存素材并返回片段。
# @router.post("/recording-sessions/{session_id}/fragments", response_model=FragmentResponse, status_code=status.HTTP_201_CREATED, summary="保存语音片段和 ASR 转写")
# def create_fragment(session_id: int, body: FragmentCreate, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)], storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)]):
#     fragment = service.create_fragment(db, user, session_id, body)
#     return service.get_fragment(db, user, session_id, fragment.id, storage)
# [新增] 保存片段与登记任务共用事务，提交后才调用模型。
@router.post("/recording-sessions/{session_id}/fragments", response_model=FragmentCreatedResponse, status_code=status.HTTP_201_CREATED, summary="保存素材片段并尝试记忆抽取")
def create_fragment(
    session_id: int, body: FragmentCreate, user: CurrentUser, db: Annotated[Session, Depends(get_db)],
    service: Annotated[MemoryService, Depends(MemoryService)],
    storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)],
    extraction: Annotated[MemoryExtractionService, Depends(get_memory_extraction_service)],
):
    job_id = None
    created = False
    skip_reason = None

    def enqueue(fragment):
        nonlocal job_id, created, skip_reason
        if not extraction.model.available:
            skip_reason = "EXTRACTION_UNAVAILABLE"
            return
        try:
            job_id, created = extraction.enqueue(
                db, user, session_id, fragment.id,
                ExtractionRequest(allow_external_processing=body.allow_external_processing),
            )
        except HTTPException as exc:
            if exc.status_code not in {403, 404, 409, 422, 503}:
                raise
            skip_reason = exc.detail.get("code", "SOURCE_ACCESS_LOST") if isinstance(exc.detail, dict) else "SOURCE_ACCESS_LOST"

    fragment = service.create_fragment(db, user, session_id, body, after_flush=enqueue)
    if created:
        extraction.run(db, job_id=job_id, storage=storage)
    result = service.get_fragment(db, user, session_id, fragment.id, storage)
    if job_id is not None:
        result["extraction_job"] = extraction.get_job(db, user, session_id, job_id)
    result["extraction_skip_reason"] = skip_reason
    return result


@router.get("/recording-sessions/{session_id}/fragments", response_model=FragmentPage, summary="分页查询语音片段")
def list_fragments(session_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)], storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)], page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 20):
    return service.list_fragments(db, user, session_id, page, page_size, storage)


@router.get("/recording-sessions/{session_id}/fragments/{fragment_id}", response_model=FragmentResponse, summary="获取语音片段详情")
def get_fragment(session_id: int, fragment_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)], storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)]):
    return service.get_fragment(db, user, session_id, fragment_id, storage)


@router.patch("/recording-sessions/{session_id}/fragments/{fragment_id}", response_model=FragmentResponse, summary="编辑语音转写文本")
def patch_fragment(session_id: int, fragment_id: int, body: FragmentPatch, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)], storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)]):
    service.patch_fragment(db, user, session_id, fragment_id, body)
    return service.get_fragment(db, user, session_id, fragment_id, storage)


@router.delete("/recording-sessions/{session_id}/fragments/{fragment_id}", response_model=DeleteResponse, summary="删除语音片段")
def delete_fragment(session_id: int, fragment_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)]):
    service.delete_fragment(db, user, session_id, fragment_id)
    return {"id": fragment_id, "deleted": True}


@router.post("/memory-fragments/{fragment_id}/attachments", response_model=list[AttachmentResponse], status_code=status.HTTP_201_CREATED, summary="批量添加片段附件")
def create_attachments(fragment_id: int, body: AttachmentBatchCreate, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)], storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)]):
    return service.create_attachments(db, user, fragment_id, body, storage)


@router.get("/memory-fragments/{fragment_id}/attachments", response_model=list[AttachmentResponse], summary="查询片段附件")
def list_attachments(fragment_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)], storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)]):
    return service.list_attachments(db, user, fragment_id, storage)


@router.patch("/memory-fragments/{fragment_id}/attachments/{attachment_id}", response_model=AttachmentResponse, summary="更新附件说明或顺序")
def patch_attachment(fragment_id: int, attachment_id: int, body: AttachmentPatch, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)], storage: Annotated[ObjectStorageService, Depends(get_object_storage_service)]):
    return service.patch_attachment(db, user, fragment_id, attachment_id, body, storage)


@router.delete("/memory-fragments/{fragment_id}/attachments/{attachment_id}", response_model=DeleteResponse, summary="从片段移除附件")
def delete_attachment(fragment_id: int, attachment_id: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)], service: Annotated[MemoryService, Depends(MemoryService)]):
    service.delete_attachment(db, user, fragment_id, attachment_id)
    return {"id": attachment_id, "deleted": True}
