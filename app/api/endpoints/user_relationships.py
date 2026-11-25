"""Endpoints for registered-user relationship invitations."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.db.database import get_db
from app.schemas.user_relationship import (
    RelationshipCreateRequest,
    RelationshipListResponse,
    RelationshipRecordResponse,
    RelationshipSort,
)
from app.services.user_relationship_service import UserRelationshipService

router = APIRouter(prefix="/users/me/relationships", tags=["user relationships"])


@router.get("", response_model=RelationshipListResponse, summary="查询我的用户关系")
def list_relationships(
    current_user: CurrentUser,
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[UserRelationshipService, Depends(UserRelationshipService)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    # 前端可获取全量关系组，或在分页前按对方昵称排序。
    paginate: Annotated[bool, Query(description="设为 false 时一次返回全部关系组")] = True,
    sort: Annotated[RelationshipSort, Query(description="默认按关系组创建时间倒序")] = "default",
):
    return service.list_for_user(
        session, current_user, page, page_size, paginate=paginate, sort=sort
    )


@router.post(
    "",
    response_model=RelationshipRecordResponse,
    status_code=status.HTTP_201_CREATED,
    summary="发起用户关系邀请",
)
def create_relationship(
    body: RelationshipCreateRequest,
    current_user: CurrentUser,
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[UserRelationshipService, Depends(UserRelationshipService)],
):
    return service.create(session, current_user, body)


@router.post(
    "/{relationship_id}/accept",
    response_model=list[RelationshipRecordResponse],
    summary="接受用户关系邀请",
)
def accept_relationship(
    relationship_id: int,
    current_user: CurrentUser,
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[UserRelationshipService, Depends(UserRelationshipService)],
):
    return service.accept(session, current_user, relationship_id)


@router.post(
    "/{relationship_id}/reject",
    response_model=RelationshipRecordResponse,
    summary="拒绝用户关系邀请",
)
def reject_relationship(
    relationship_id: int,
    current_user: CurrentUser,
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[UserRelationshipService, Depends(UserRelationshipService)],
):
    return service.reject(session, current_user, relationship_id)


@router.post(
    "/{relationship_id}/revoke",
    response_model=list[RelationshipRecordResponse],
    summary="撤回待确认邀请或撤销有效关系",
)
def revoke_relationship(
    relationship_id: int,
    current_user: CurrentUser,
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[UserRelationshipService, Depends(UserRelationshipService)],
):
    return service.revoke(session, current_user, relationship_id)
