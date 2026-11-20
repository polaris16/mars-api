"""[新增] 回忆录冲突核对与跨会话复验接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.db.database import get_db
from app.schemas.memory_review import (
    QuestionStatus,
    ReopenQuestion,
    ReviewAnswerAccepted,
    ReviewAnswerCreate,
    ReviewQuestionPage,
    ValidationJobAccepted,
    ValidationJobCreate,
    ValidationJobResponse,
)
from app.services.memory_review_service import MemoryReviewService

router = APIRouter(prefix="/users/me", tags=["memory review"])
Db = Annotated[Session, Depends(get_db)]
Review = Annotated[MemoryReviewService, Depends(MemoryReviewService)]
ResourceId = Annotated[int, Path(gt=0, le=2**63 - 1)]
SessionId = Annotated[int, Query(gt=0, le=2**63 - 1)]
Page = Annotated[int, Query(ge=1, le=2**31 - 1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get(
    "/memory-review-questions", response_model=ReviewQuestionPage, summary="查询记忆核对问题"
)
def list_memory_review_questions(
    user: CurrentUser,
    db: Db,
    service: Review,
    page: Page = 1,
    page_size: PageSize = 20,
    session_id: SessionId | None = None,
    status: QuestionStatus = "pending",
):
    return service.list_questions(db, user, page, page_size, status, session_id)


@router.post(
    "/memory-review-questions/{question_id}/answers",
    response_model=ReviewAnswerAccepted,
    status_code=202,
    summary="提交记忆核对回答",
)
def answer_memory_review_question(
    question_id: ResourceId,
    body: ReviewAnswerCreate,
    user: CurrentUser,
    db: Db,
    service: Review,
):
    return service.answer(db, user, question_id, body)


@router.post(
    "/memory-review-questions/{question_id}/reopen",
    response_model=ValidationJobAccepted,
    status_code=202,
    summary="重新开启已跳过或记不清的问题",
)
def reopen_memory_review_question(
    question_id: ResourceId,
    body: ReopenQuestion,
    user: CurrentUser,
    db: Db,
    service: Review,
):
    return service.reopen(db, user, question_id, body.expected_revision)


@router.get(
    "/memory-validation-jobs/{job_id}",
    response_model=ValidationJobResponse,
    summary="查询记忆复验任务",
)
def get_memory_validation_job(job_id: ResourceId, user: CurrentUser, db: Db, service: Review):
    return service.get_job(db, user, job_id)


@router.post(
    "/memory-validation-jobs",
    response_model=ValidationJobAccepted,
    status_code=202,
    summary="提交记忆核验、重构或重试任务",
)
def create_memory_validation_job(
    body: ValidationJobCreate,
    user: CurrentUser,
    db: Db,
    service: Review,
):
    return service.submit_job(db, user, body)
