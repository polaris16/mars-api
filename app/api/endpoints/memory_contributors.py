"""记忆会话素材提供者管理接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser, Paginate
from app.db.database import get_db
from app.schemas.memory_contributor import (
    ContributorConsentRequest,
    ContributorPage,
    ContributorRegisterRequest,
    ContributorResponse,
    ContributorSort,
)
from app.services.memory_contributor_service import MemoryContributorService

router = APIRouter(
    prefix="/recording-sessions/{session_id}/contributors", tags=["memory contributors"]
)
SessionId = Annotated[int, Path(gt=0)]
DbSession = Annotated[Session, Depends(get_db)]
ContributorService = Annotated[MemoryContributorService, Depends(MemoryContributorService)]


@router.get("", response_model=ContributorPage, summary="分页查询素材提供者")
def list_contributors(
    session_id: SessionId,
    user: CurrentUser,
    db: DbSession,
    service: ContributorService,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    paginate: Paginate = True,
    sort: ContributorSort = "default",
) -> ContributorPage:
    # [删除-旧] return service.list_contributors(db, user, session_id, page, page_size)
    return service.list_contributors(
        db, user, session_id, page, page_size, paginate=paginate, sort=sort
    )


@router.post(
    "",
    response_model=ContributorResponse,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": ContributorResponse}},
    summary="登记素材提供者",
)
def register_contributor(
    session_id: SessionId,
    body: ContributorRegisterRequest,
    response: Response,
    user: CurrentUser,
    db: DbSession,
    service: ContributorService,
) -> ContributorResponse:
    contributor, created = service.register(db, user, session_id, body.user_id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return contributor


@router.get("/me", response_model=ContributorResponse, summary="查询我的素材提供者登记")
def get_my_registration(
    session_id: SessionId,
    user: CurrentUser,
    db: DbSession,
    service: ContributorService,
) -> ContributorResponse:
    return service.get_my_registration(db, user, session_id)


@router.patch(
    "/{contributor_id}/consent",
    response_model=ContributorResponse,
    summary="更新本人素材提供同意状态",
)
def set_my_consent(
    session_id: SessionId,
    contributor_id: Annotated[int, Path(gt=0)],
    body: ContributorConsentRequest,
    user: CurrentUser,
    db: DbSession,
    service: ContributorService,
) -> ContributorResponse:
    return service.set_my_consent(db, user, session_id, contributor_id, body.consent_status)
