"""[新增] 录音会话、片段、OSS 媒体和附件接口模型。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
Sha256 = Annotated[str, StringConstraints(pattern=r"^[A-Fa-f0-9]{64}$")]


class PageResponse(BaseModel):
    page: int
    page_size: int
    total: int


class SessionCreate(BaseModel):
    topic_id: int | None = None
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] = "未命名"


class SessionPatch(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    topic_id: int | None = None
    is_favorite: bool | None = None

    @model_validator(mode="after")
    def validate_non_nullable_fields(self):
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title 不能为 null")
        if "is_favorite" in self.model_fields_set and self.is_favorite is None:
            raise ValueError("is_favorite 不能为 null")
        return self


class SessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    topic_id: int | None
    topic_title: str | None = None
    title: str
    is_favorite: bool
    last_recorded_at: datetime | None
    created_at: datetime
    fragment_count: int = 0
    attachment_count: int = 0
    duration_ms: int = 0
    transcript_excerpt: str | None = None


class SessionPage(PageResponse):
    items: list[SessionSummary]


class UploadAuthorizationRequest(BaseModel):
    purpose: Literal["recording", "attachment"]
    media_type: Literal["audio", "image", "video"]
    filename: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    content_type: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    size_bytes: int = Field(gt=0)
    sha256: Sha256 | None = None
    session_id: int | None = None
    fragment_id: int | None = None


class UploadAuthorizationResponse(BaseModel):
    reused: bool
    asset_id: int
    upload_method: Literal["PUT"] | None = None
    upload_url: str | None = None
    required_headers: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime | None = None


class UploadCompleteRequest(BaseModel):
    duration_ms: int | None = Field(default=None, ge=0)
    sample_rate_hz: int | None = Field(default=None, gt=0)


class MediaAssetResponse(BaseModel):
    id: int
    purpose: str
    media_type: str
    filename: str | None
    content_type: str
    size_bytes: int | None
    duration_ms: int | None
    sample_rate_hz: int | None
    upload_status: str
    created_at: datetime
    download_url: str | None = None
    url_expires_at: datetime | None = None


class MediaAssetPage(PageResponse):
    items: list[MediaAssetResponse]


class FragmentCreate(BaseModel):
    audio_asset_id: int
    transcript_raw: str | None = None
    recorded_at: datetime | None = None


class FragmentPatch(BaseModel):
    transcript_edited: str | None


class AttachmentInput(BaseModel):
    asset_id: int
    caption: Annotated[str, StringConstraints(max_length=200)] | None = None
    sort_order: int = Field(default=0, ge=0)


class AttachmentBatchCreate(BaseModel):
    items: list[AttachmentInput] = Field(min_length=1, max_length=9)


class AttachmentPatch(BaseModel):
    caption: Annotated[str, StringConstraints(max_length=200)] | None = None
    sort_order: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_sort_order(self):
        if "sort_order" in self.model_fields_set and self.sort_order is None:
            raise ValueError("sort_order 不能为 null")
        return self


class AttachmentResponse(BaseModel):
    id: int
    fragment_id: int
    asset: MediaAssetResponse
    caption: str | None
    sort_order: int
    created_at: datetime


class FragmentResponse(BaseModel):
    id: int
    session_id: int
    sequence_no: int
    transcript_raw: str | None
    transcript_edited: str | None
    recorded_at: datetime
    audio: MediaAssetResponse
    attachments: list[AttachmentResponse] = Field(default_factory=list)


class FragmentPage(PageResponse):
    items: list[FragmentResponse]


class SessionDetail(SessionSummary):
    fragments: list[FragmentResponse]


class DeleteResponse(BaseModel):
    id: int
    deleted: bool
