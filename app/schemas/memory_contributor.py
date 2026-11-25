"""记忆会话素材提供者的登记和同意状态。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 提供者可按登记 ID 排序。
ContributorSort = Literal["default", "id_asc", "id_desc"]


class ContributorRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)


class ContributorConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consent_status: Literal["accepted", "withdrawn"]


class ContributorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    account_user_id: int
    contributor_type: Literal["owner", "registered_user"]
    session_member_id: int | None
    relationship_id: int | None
    display_name: str
    identity_status: Literal["unverified", "verified"]
    consent_status: Literal["pending", "accepted", "withdrawn"]
    consented_at: datetime | None
    is_eligible: bool = False


class ContributorPage(BaseModel):
    items: list[ContributorResponse]
    page: int
    page_size: int
    total: int
