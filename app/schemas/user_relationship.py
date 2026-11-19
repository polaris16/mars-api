"""Requests and responses for registered-user relationships."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing import Annotated


RelationshipType = Literal["family", "agent", "caregiver", "other"]
RelationCode = Literal[
    "parent_of", "child_of", "spouse_of", "sibling_of", "related_to",
    "agent_for", "caregiver_for", "other",
]


class RelationshipCreateRequest(BaseModel):
    related_user_id: int = Field(gt=0)
    relationship_type: RelationshipType
    relation_code: RelationCode
    relationship_label: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
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
