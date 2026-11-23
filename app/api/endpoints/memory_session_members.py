"""记忆会话成员授权接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser, Paginate
from app.db.database import get_db
from app.schemas.memory_session_member import (
    MemberGrantRequest,
    MemberPage,
    MemberResponse,
    MemberRevokeRequest,
    MemberSort,
)
from app.services.memory_session_member_service import MemorySessionMemberService

router = APIRouter(
    prefix="/recording-sessions/{session_id}/members", tags=["memory session members"]
)
SessionId = Annotated[int, Path(gt=0)]
DbSession = Annotated[Session, Depends(get_db)]
MemberService = Annotated[MemorySessionMemberService, Depends(MemorySessionMemberService)]


@router.get("", response_model=MemberPage, summary="查询会话成员授权")
def list_members(
    session_id: SessionId,
    user: CurrentUser,
    db: DbSession,
    service: MemberService,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    paginate: Paginate = True,
    sort: MemberSort = "default",
) -> MemberPage:
    # [删除-旧] return service.list_members(db, user, session_id, page, page_size)
    return service.list_members(db, user, session_id, page, page_size, paginate=paginate, sort=sort)


@router.post(
    "",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="授予会话成员权限",
)
def grant_member(
    session_id: SessionId,
    body: MemberGrantRequest,
    user: CurrentUser,
    db: DbSession,
    service: MemberService,
) -> MemberResponse:
    return service.grant(db, user, session_id, body)


@router.post("/{member_id}/revoke", response_model=MemberResponse, summary="撤销会话成员授权")
def revoke_member(
    session_id: SessionId,
    member_id: Annotated[int, Path(gt=0)],
    user: CurrentUser,
    db: DbSession,
    service: MemberService,
    body: MemberRevokeRequest | None = None,
) -> MemberResponse:
    return service.revoke(
        db, user, session_id, member_id, body.reason if body is not None else None
    )
