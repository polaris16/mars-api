"""记忆抽取任务、结构化元素和原文证据 API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser, PaginateOnly
from app.db.database import get_db
from app.schemas.list_sort import CreatedAtSort, IdSort
from app.schemas.memory_extraction import (
    ElementOrigin,
    ElementType,
    ExtractionJobPage,
    ExtractionJobResponse,
    ExtractionRequest,
    Id,
    MemoryElementPage,
    MemoryElementResponse,
    MemoryProvenanceResponse,
    ReviewStatus,
    VerificationStatus,
)
from app.services.memory_extraction_service import (
    MemoryExtractionService,
    get_memory_extraction_service,
)

router = APIRouter(prefix="/recording-sessions/{session_id}", tags=["memory extraction"])
DbSession = Annotated[Session, Depends(get_db)]
ExtractionService = Annotated[MemoryExtractionService, Depends(get_memory_extraction_service)]
PathId = Annotated[int, Path(gt=0, le=2**63 - 1)]
Page = Annotated[int, Query(ge=1, le=2**31 - 1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.post(
    "/fragments/{fragment_id}/extraction-jobs",
    response_model=ExtractionJobResponse,
    responses={202: {"model": ExtractionJobResponse}},
    summary="提交记忆元素抽取任务",
)
def submit_extraction(
    session_id: PathId,
    fragment_id: PathId,
    response: Response,
    user: CurrentUser,
    db: DbSession,
    service: ExtractionService,
    body: ExtractionRequest,
) -> ExtractionJobResponse:
    with db.begin():
        job_id, created = service.enqueue(db, user, session_id, fragment_id, body)
    if created:
        service.run(db, job_id=job_id)
    job = service.get_job(db, user, session_id, job_id)
    if job.status in {"queued", "processing"}:
        response.status_code = 202
    return job


@router.get(
    "/fragments/{fragment_id}/extraction-jobs",
    response_model=ExtractionJobPage,
    summary="分页查询片段抽取任务",
)
def list_extraction_jobs(
    session_id: PathId,
    fragment_id: PathId,
    user: CurrentUser,
    db: DbSession,
    service: ExtractionService,
    page: Page = 1,
    page_size: PageSize = 20,
    paginate: PaginateOnly = True,
    sort: CreatedAtSort = "default",
) -> ExtractionJobPage:
    return service.list_jobs(db, user, session_id, fragment_id, page, page_size, sort=sort)


@router.get(
    "/extraction-jobs/{job_id}", response_model=ExtractionJobResponse, summary="查询抽取任务状态"
)
def get_extraction_job(
    session_id: PathId,
    job_id: PathId,
    user: CurrentUser,
    db: DbSession,
    service: ExtractionService,
) -> ExtractionJobResponse:
    return service.get_job(db, user, session_id, job_id)


@router.get("/memory-elements", response_model=MemoryElementPage, summary="分页查询会话记忆元素")
def list_memory_elements(
    session_id: PathId,
    user: CurrentUser,
    db: DbSession,
    service: ExtractionService,
    page: Page = 1,
    page_size: PageSize = 20,
    source_fragment_id: Id | None = None,
    processing_job_id: Id | None = None,
    element_type: ElementType | None = None,
    review_status: ReviewStatus | None = None,
    origin: ElementOrigin | None = None,
    verification_status: VerificationStatus | None = None,
    paginate: PaginateOnly = True,
    sort: IdSort = "default",
) -> MemoryElementPage:
    return service.list_elements(
        db,
        user,
        session_id,
        page,
        page_size,
        source_fragment_id=source_fragment_id,
        processing_job_id=processing_job_id,
        element_type=element_type,
        review_status=review_status,
        origin=origin,
        verification_status=verification_status,
        sort=sort,
    )


@router.get(
    "/memory-elements/{element_id}", response_model=MemoryElementResponse, summary="查询记忆元素"
)
def get_memory_element(
    session_id: PathId,
    element_id: PathId,
    user: CurrentUser,
    db: DbSession,
    service: ExtractionService,
) -> MemoryElementResponse:
    return service.get_element(db, user, session_id, element_id)


@router.get(
    "/memory-elements/{element_id}/provenance",
    response_model=MemoryProvenanceResponse,
    summary="查询记忆元素的原文证据",
)
def get_memory_provenance(
    session_id: PathId,
    element_id: PathId,
    user: CurrentUser,
    db: DbSession,
    service: ExtractionService,
) -> MemoryProvenanceResponse:
    return service.get_provenance(db, user, session_id, element_id)
