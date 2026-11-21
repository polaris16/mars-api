"""[新增] 已有记忆片段的附件批量上传契约。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

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
    # [新增] 仅在询问模式下提交对重复项的决定和共享上传的代表项。
    duplicate_action: Literal["skip", "keep"] | None = None
    representative_client_file_id: UUID | None = None


class AttachmentUploadBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AttachmentUploadItem] = Field(min_length=1, max_length=9)
    # [新增] 由 App 显式启用询问，默认沿用已有请求的自动复用。
    duplicate_mode: Literal["reuse", "ask"] = "reuse"

    @field_validator("items")
    @classmethod
    def unique_file_ids(cls, items: list[AttachmentUploadItem]) -> list[AttachmentUploadItem]:
        if len({item.client_file_id for item in items}) != len(items):
            raise ValueError("同一批次的 client_file_id 必须唯一")
        return items


class AttachmentCompleteBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AttachmentCompleteItem] = Field(min_length=1, max_length=9)
    # [新增] 与批量授权的询问模式一致。
    duplicate_mode: Literal["reuse", "ask"] = "reuse"

    @field_validator("items")
    @classmethod
    def unique_file_ids(cls, items: list[AttachmentCompleteItem]) -> list[AttachmentCompleteItem]:
        if len({item.client_file_id for item in items}) != len(items):
            raise ValueError("同一批次的 client_file_id 必须唯一")
        return items

    @model_validator(mode="after")
    def validate_duplicate_choices(self) -> "AttachmentCompleteBatch":
        items_by_id = {item.client_file_id: item for item in self.items}
        for item in self.items:
            representative_id = item.representative_client_file_id
            if self.duplicate_mode == "reuse" and (item.duplicate_action or representative_id):
                raise ValueError("重复项决定和代表项仅适用于 duplicate_mode=ask")
            representative = items_by_id.get(representative_id) if representative_id else None
            if representative is not None and (
                representative.representative_client_file_id is not None
                or representative.asset_id != item.asset_id
            ):
                raise ValueError("代表项须直接引用同一个 asset_id，不能指向其他项")
        return self


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


# [新增] 授权重放已成功的操作时返回原附件关联。
class AttachmentAlreadyBound(BaseModel):
    client_file_id: UUID
    status: Literal["already_attached"]
    asset_id: int
    attachment_id: int


# [新增] 重复授权结果携带可复用的素材和识别原关联或代表项所需的信息。
class AttachmentUploadDuplicate(BaseModel):
    client_file_id: UUID
    status: Literal["duplicate"]
    asset_id: int
    reason: Literal["existing_attachment", "same_batch", "pending_upload"]
    attachment_id: int | None = None
    representative_client_file_id: UUID | None = None
    expires_at: datetime | None = None


class AttachmentUploadBatchResponse(BaseModel):
    items: list[
        Annotated[
            # [删除-旧] AttachmentUploadRequired | AttachmentUploadReused | AttachmentItemFailed,
            AttachmentUploadRequired
            | AttachmentUploadReused
            | AttachmentUploadDuplicate
            | AttachmentAlreadyBound
            | AttachmentItemFailed,
            Field(discriminator="status"),
        ]
    ]


class AttachmentBound(BaseModel):
    client_file_id: UUID
    status: Literal["attached", "already_attached"]
    asset_id: int
    attachment_id: int


# [新增] 确认时可能发现新重复，也可能接受明确的跳过决定。
class AttachmentDuplicateDecision(BaseModel):
    client_file_id: UUID
    status: Literal["duplicate", "skipped"]
    asset_id: int
    attachment_id: int | None = None


class AttachmentCompleteBatchResponse(BaseModel):
    # [删除-旧] items: list[Annotated[AttachmentBound | AttachmentItemFailed, Field(discriminator="status")]]
    items: list[
        Annotated[
            AttachmentBound | AttachmentDuplicateDecision | AttachmentItemFailed,
            Field(discriminator="status"),
        ]
    ]
