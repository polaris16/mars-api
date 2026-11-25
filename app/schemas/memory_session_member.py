"""记忆会话成员授权的请求和响应。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

MemberRole = Literal["viewer", "contributor", "editor", "delegate"]
# 会话成员可按授权时间排序。
MemberSort = Literal["default", "granted_at_asc", "granted_at_desc"]


class MemberGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)
    role: MemberRole
    source_relationship_id: int | None = Field(default=None, gt=0)
    can_manage_members: bool = False
    valid_until: AwareDatetime | None = None


class MemberRevokeRequest(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] | None = None


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    user_id: int
    role: MemberRole
    source_relationship_id: int | None
    can_manage_members: bool
    status: Literal["active", "revoked", "expired"]
    is_effective: bool = False
    granted_by_user_id: int
    granted_at: datetime
    valid_until: datetime | None
    revoked_at: datetime | None
    revocation_reason: str | None


class MemberPage(BaseModel):
    items: list[MemberResponse]
    page: int
    page_size: int
    total: int
