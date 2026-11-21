"""[新增] App 联系人、好友申请和邀请码的 HTTP 接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser, bearer_scheme
from app.db.database import get_db
from app.schemas.user_relationship import (
    ContactCodeResponse,
    ContactListResponse,
    ContactRequest,
    ContactRequestDirection,
    ContactRequestListResponse,
    ContactRequestStatus,
    ContactSearchRequest,
    ContactSearchResponse,
    ContactView,
    RelationshipCreateRequest,
    RelationshipRecordResponse,
)
from app.services.contact_invitation_service import ContactInvitationService
from app.services.user_relationship_service import UserRelationshipService

router = APIRouter(prefix="/users/me/contacts", tags=["contacts"])
Db = Annotated[Session, Depends(get_db)]
Relationships = Annotated[UserRelationshipService, Depends(UserRelationshipService)]
Codes = Annotated[ContactInvitationService, Depends(ContactInvitationService)]
Bearer = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


@router.get("", response_model=ContactListResponse, summary="查询联系人和待处理申请")
def list_contacts(
    user: CurrentUser,
    db: Db,
    relationships: Relationships,
    view: ContactView = "active",
    page: Annotated[int, Query(ge=1, le=2**31 - 1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(max_length=100)] = None,
):
    return relationships.list_contacts(db, user.id, view, page, page_size, keyword)


# [新增] 搜索候选和申请历史独立于已有联系人列表。
@router.post("/search", response_model=ContactSearchResponse, summary="搜索好友候选")
def search_contact_candidates(
    body: ContactSearchRequest, user: CurrentUser, db: Db, relationships: Relationships
):
    return relationships.search_contact_candidates(db, user.id, body)


@router.get("/requests", response_model=ContactRequestListResponse, summary="查询收发好友申请")
def list_contact_requests(
    user: CurrentUser,
    db: Db,
    relationships: Relationships,
    direction: ContactRequestDirection,
    status: ContactRequestStatus | None = None,
    page: Annotated[int, Query(ge=1, le=2**31 - 1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return relationships.list_contact_requests(db, user.id, direction, status, page, page_size)


@router.post("/invite-code", response_model=ContactCodeResponse, summary="获取我的联系人邀请码")
def get_contact_invite_code(
    user: CurrentUser, db: Db, credentials: Bearer, response: Response, codes: Codes
):
    if credentials is None:
        raise HTTPException(401, "请先登录")
    response.headers["Cache-Control"] = "no-store"
    return codes.issue(db, user.id, credentials.credentials)


@router.post(
    "/requests",
    response_model=RelationshipRecordResponse,
    status_code=201,
    # [删除-旧] summary="使用邀请码发起好友申请",
    summary="向候选人或邀请码持有者发起好友申请",
)
def send_contact_request(
    body: ContactRequest, user: CurrentUser, db: Db, codes: Codes, relationships: Relationships
):
    # [删除-旧] target = codes.target_user_id(db, body.code)
    target = body.related_user_id if body.code is None else codes.target_user_id(db, body.code)
    assert target is not None  # 请求模型确保提供一种有效的目标标识。
    return relationships.create(
        db,
        user,
        RelationshipCreateRequest(
            related_user_id=target, relationship_type="contact", relation_code="contact_of"
        ),
        request_message=body.message,
    )
