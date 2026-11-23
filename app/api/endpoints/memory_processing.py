"""[新增] 版本化记忆进度、整理稿和实体确认接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Response
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser, PaginateOnly
from app.db.database import get_db
from app.schemas.list_sort import IdSort, NameSort
from app.schemas.memory_processing import (
    CandidateResolutionCreate,
    CandidateResolutionResponse,
    DraftVersionCreate,
    DraftVersionPage,
    DraftVersionResponse,
    EntityCandidatePage,
    EntityDetail,
    EntityPage,
    EntityType,
    ProcessingJobCreate,
    ProcessingJobResponse,
    ProcessingResponse,
)
from app.services.memory_entities import MemoryEntityService
from app.services.memory_pipeline import MemoryPipelineService


router = APIRouter(prefix="/recording-sessions/{session_id}", tags=["memory processing"])
FRAGMENT = "/fragments/{fragment_id}"
Db = Annotated[Session, Depends(get_db)]
Id = Annotated[int, Path(gt=0, le=2**63 - 1)]
QueryId = Annotated[int, Query(gt=0, le=2**63 - 1)]
Page = Annotated[int, Query(ge=1, le=2**31 - 1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def get_pipeline() -> MemoryPipelineService:
    return MemoryPipelineService()


Pipeline = Annotated[MemoryPipelineService, Depends(get_pipeline)]
Entities = Annotated[MemoryEntityService, Depends(MemoryEntityService)]


@router.get(f"{FRAGMENT}/processing", response_model=ProcessingResponse, summary="查询素材处理进度")
def get_processing(session_id: Id, fragment_id: Id, user: CurrentUser, db: Db, service: Pipeline):
    return service.progress(db, user, session_id, fragment_id)


@router.post(
    f"{FRAGMENT}/processing-jobs", response_model=ProcessingJobResponse, status_code=202,
    responses={200: {"model": ProcessingJobResponse}}, summary="提交或补跑 ASR 与整理任务",
)
def create_processing_job(
    session_id: Id, fragment_id: Id, body: ProcessingJobCreate, response: Response,
    user: CurrentUser, db: Db, service: Pipeline,
):
    result = service.submit(db, user, session_id, fragment_id, body)
    if result["status"] not in {"queued", "processing"}:
        response.status_code = 200
    return result


@router.get(f"{FRAGMENT}/draft-versions", response_model=DraftVersionPage, summary="查询整理稿版本")
def list_drafts(
    session_id: Id,
    fragment_id: Id,
    user: CurrentUser,
    db: Db,
    service: Pipeline,
    page: Page = 1,
    page_size: PageSize = 20,
    paginate: PaginateOnly = True,
    sort: IdSort = "default",
):
    # [删除-旧] return service.list_drafts(db, user, session_id, fragment_id, page, page_size)
    return service.list_drafts(db, user, session_id, fragment_id, page, page_size, sort=sort)


@router.get(
    f"{FRAGMENT}/draft-versions/{{version_id}}", response_model=DraftVersionResponse,
    summary="查看整理稿及原文位置",
)
def get_draft(
    session_id: Id, fragment_id: Id, version_id: Id, user: CurrentUser, db: Db, service: Pipeline,
):
    return service.get_draft(db, user, session_id, fragment_id, version_id)


@router.post(
    f"{FRAGMENT}/draft-versions", response_model=DraftVersionResponse,
    status_code=201, summary="保存人工校订的整理稿版本",
)
def save_draft(
    session_id: Id, fragment_id: Id, body: DraftVersionCreate, user: CurrentUser, db: Db,
    service: Pipeline, idempotency_key: Annotated[UUID | None, Header()] = None,
):
    return service.save_draft(db, user, session_id, fragment_id, body, idempotency_key)


@router.get("/memory-entities", response_model=EntityPage, summary="查询会话证据支持的实体")
def list_entities(
    session_id: Id,
    user: CurrentUser,
    db: Db,
    service: Entities,
    page: Page = 1,
    page_size: PageSize = 20,
    entity_type: EntityType | None = None,
    element_id: QueryId | None = None,
    paginate: PaginateOnly = True,
    sort: NameSort = "default",
):
    # [删除-旧] return service.list_entities(db, user, session_id, page, page_size, entity_type, element_id)
    return service.list_entities(
        db, user, session_id, page, page_size, entity_type, element_id, sort=sort
    )


@router.get("/memory-entities/{entity_id}", response_model=EntityDetail, summary="查询实体及可见别名")
def get_entity(session_id: Id, entity_id: Id, user: CurrentUser, db: Db, service: Entities):
    return service.get_entity(db, user, session_id, entity_id)


@router.get("/entity-candidates", response_model=EntityCandidatePage, summary="查询实体待确认项")
def list_candidates(
    session_id: Id,
    user: CurrentUser,
    db: Db,
    service: Entities,
    page: Page = 1,
    page_size: PageSize = 20,
    element_id: QueryId | None = None,
    paginate: PaginateOnly = True,
    sort: IdSort = "default",
):
    # [删除-旧] return service.list_candidates(db, user, session_id, page, page_size, element_id)
    return service.list_candidates(db, user, session_id, page, page_size, element_id, sort=sort)


@router.post(
    "/entity-candidates/{candidate_id}/resolutions", response_model=CandidateResolutionResponse,
    status_code=201, summary="确认或排除实体候选",
)
def resolve_candidate(
    session_id: Id, candidate_id: Id, body: CandidateResolutionCreate, user: CurrentUser,
    db: Db, service: Entities,
):
    return service.resolve(db, user, session_id, candidate_id, body)
