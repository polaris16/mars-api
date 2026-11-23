"""Requests and responses for registered-user relationships."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.list_sort import NameSort

# [删除-旧] RelationshipType = Literal["family", "agent", "caregiver", "other"]
RelationshipType = Literal["family", "agent", "caregiver", "other", "contact"]
# [新增] 省略排序时保留各列表的既有顺序。
RelationshipSort = Literal["default", "name_asc", "name_desc"]
RelationCode = Literal[
    "parent_of",
    "child_of",
    "spouse_of",
    "sibling_of",
    "related_to",
    "agent_for",
    "caregiver_for",
    "other",
    "contact_of",
]


class RelationshipCreateRequest(BaseModel):
    related_user_id: int = Field(gt=0)
    relationship_type: RelationshipType
    relation_code: RelationCode
    relationship_label: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None
    ) = None
    valid_until: datetime | None = None


class RelationshipRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    relationship_pair_id: UUID
    subject_user_id: int
    related_user_id: int
    relationship_type: str
    relation_code: str
    relationship_label: str | None
    status: str
    initiated_by_user_id: int
    accepted_at: datetime | None
    invitation_expires_at: datetime | None
    valid_from: datetime | None
    valid_until: datetime | None
    revoked_at: datetime | None


class RelationshipListResponse(BaseModel):
    items: list[RelationshipRecordResponse]
    page: int
    page_size: int
    total: int


# [新增] 联系人列表仅返回可展示的用户资料。
ContactView = Literal["active", "incoming", "outgoing"]
ContactCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, pattern=r"^[1-9][0-9]{0,18}\.[1-9][0-9]{0,11}\.[0-9a-f]{64}$"
    ),
]


class ContactCodeResponse(BaseModel):
    code: ContactCode
    expires_at: datetime


class ContactRequest(BaseModel):
    # [删除-旧] code: ContactCode
    # [新增] 原邀请码请求可继续使用 code，搜索结果使用用户 ID 与留言。
    code: ContactCode | None = None
    related_user_id: int | None = Field(default=None, strict=True, gt=0, le=2**63 - 1)
    message: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[^\x00]+$"
            ),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_target(self) -> "ContactRequest":
        if (self.code is None) == (self.related_user_id is None):
            raise ValueError("请选择一位候选用户，或填写一个邀请码")
        if self.related_user_id is not None and self.message is None:
            raise ValueError("请填写好友申请留言")
        return self


class ContactProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str | None
    avatar_url: str | None


class ContactRecordResponse(RelationshipRecordResponse):
    contact_user: ContactProfile


class ContactListResponse(RelationshipListResponse):
    items: list[ContactRecordResponse]


# [新增] 搜索词放在请求体，允许 App 提交空输入以清除结果。
class ContactSearchRequest(BaseModel):
    keyword: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=100, pattern=r"^[^\x00]*$")
    ]
    page: int = Field(default=1, ge=1, le=2**31 - 1)
    page_size: int = Field(default=20, ge=1, le=20)
    # [新增] 排序与分页参数留在请求体，候选检索始终分页。
    paginate: Literal[True] = Field(default=True, description="候选搜索仅支持分页")
    sort: NameSort = "default"


class ContactSearchResponse(BaseModel):
    items: list[ContactProfile]
    page: int
    page_size: int
    total: int


ContactRequestDirection = Literal["incoming", "outgoing"]
ContactRequestStatus = Literal["pending", "accepted", "rejected", "withdrawn", "expired"]


class ContactRequestItem(BaseModel):
    request_id: int
    contact_user: ContactProfile
    message: str | None
    status: ContactRequestStatus
    created_at: datetime
    resolved_at: datetime | None


class ContactRequestListResponse(BaseModel):
    items: list[ContactRequestItem]
    page: int
    page_size: int
    total: int
