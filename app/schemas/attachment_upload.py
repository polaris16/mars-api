"""[新增] 已有记忆片段的附件批量上传契约。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.schemas.memory import PG_BIGINT_MAX, PG_INTEGER_MAX, Sha256, UploadCompleteRequest


class AttachmentUploadItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_file_id: UUID
    filename: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=255, pattern=r"^[^\x00]+$"
        ),
    ]
    media_type: Literal["audio", "image", "video"]
    content_type: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[^\x00\r\n]+$"
        ),
    ]
    size_bytes: int = Field(gt=0, le=PG_BIGINT_MAX)
    sha256: Sha256 | None = None


class AttachmentCompleteItem(UploadCompleteRequest):
    model_config = ConfigDict(extra="forbid")

    client_file_id: UUID
    asset_id: int = Field(gt=0, le=PG_BIGINT_MAX)
    caption: Annotated[str, StringConstraints(max_length=200, pattern=r"^[^\x00]*$")] | None = None
    sort_order: int = Field(default=0, ge=0, le=PG_INTEGER_MAX)


class AttachmentUploadBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AttachmentUploadItem] = Field(min_length=1, max_length=9)

    @field_validator("items")
    @classmethod
    def unique_file_ids(cls, items: list[AttachmentUploadItem]) -> list[AttachmentUploadItem]:
        if len({item.client_file_id for item in items}) != len(items):
            raise ValueError("同一批次的 client_file_id 必须唯一")
        return items


class AttachmentCompleteBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AttachmentCompleteItem] = Field(min_length=1, max_length=9)

    @field_validator("items")
    @classmethod
    def unique_file_ids(cls, items: list[AttachmentCompleteItem]) -> list[AttachmentCompleteItem]:
        if len({item.client_file_id for item in items}) != len(items):
            raise ValueError("同一批次的 client_file_id 必须唯一")
        return items


class AttachmentUploadIssue(BaseModel):
    code: str
    message: str
    retryable: bool


class AttachmentItemFailed(BaseModel):
    client_file_id: UUID
    status: Literal["failed"]
    error: AttachmentUploadIssue


class AttachmentUploadRequired(BaseModel):
    client_file_id: UUID
    status: Literal["upload_required"]
    asset_id: int
    upload_url: str
    upload_method: Literal["PUT"]
    required_headers: dict[str, str]
    expires_at: datetime


class AttachmentUploadReused(BaseModel):
    client_file_id: UUID
    status: Literal["reused"]
    asset_id: int


class AttachmentUploadBatchResponse(BaseModel):
    items: list[
        Annotated[
            AttachmentUploadRequired | AttachmentUploadReused | AttachmentItemFailed,
            Field(discriminator="status"),
        ]
    ]


class AttachmentBound(BaseModel):
    client_file_id: UUID
    status: Literal["attached", "already_attached"]
    asset_id: int
    attachment_id: int


class AttachmentCompleteBatchResponse(BaseModel):
    items: list[Annotated[AttachmentBound | AttachmentItemFailed, Field(discriminator="status")]]
