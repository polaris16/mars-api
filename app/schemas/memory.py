"""记忆会话、片段、OSS 媒体和附件接口模型。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.memory_extraction import ExtractionJobResponse
from app.schemas.memory_processing import NarrationInput, TranscriptSegment

Sha256 = Annotated[str, StringConstraints(pattern=r"^[A-Fa-f0-9]{64}$")]
PG_INTEGER_MAX = (1 << 31) - 1
PG_BIGINT_MAX = (1 << 63) - 1
MemoryVisibility = Literal["public", "private", "secret"]
# 会话列表兼容早期通过 order 指定方向的调用。
SessionSort = Literal[
    "default",
    "last_recorded_at",
    "created_at",
    "last_recorded_at_asc",
    "last_recorded_at_desc",
    "created_at_asc",
    "created_at_desc",
]
AssetSort = Literal["default", "created_at_asc", "created_at_desc"]
FragmentSort = Literal[
    "default",
    "sequence_asc",
    "sequence_desc",
    "recorded_at_asc",
    "recorded_at_desc",
]


class PageResponse(BaseModel):
    page: int
    page_size: int
    total: int


class SessionCreate(BaseModel):
    topic_id: int | None = None
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ] = "未命名"
    visibility: MemoryVisibility | None = None


class SessionPatch(BaseModel):
    title: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
        | None
    ) = None
    topic_id: int | None = None
    is_favorite: bool | None = None
    visibility: MemoryVisibility | None = None

    @model_validator(mode="after")
    def validate_non_nullable_fields(self):
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title 不能为 null")
        if "is_favorite" in self.model_fields_set and self.is_favorite is None:
            raise ValueError("is_favorite 不能为 null")
        if "visibility" in self.model_fields_set and self.visibility is None:
            raise ValueError("visibility 不能为 null")
        return self


class SessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    topic_id: int | None
    topic_title: str | None = None
    title: str
    is_favorite: bool
    visibility: MemoryVisibility | None = None
    # [UPDATED] Accept the persisted model name and the API response name while serializing consistently.
    last_recorded_at: datetime | None = Field(
        validation_alias=AliasChoices("last_recorded_at", "last_material_at")
    )
    created_at: datetime
    fragment_count: int = 0
    attachment_count: int = 0
    duration_ms: int = 0
    transcript_excerpt: str | None = None


class SessionPage(PageResponse):
    items: list[SessionSummary]


class UploadAuthorizationRequest(BaseModel):
    purpose: Literal["recording", "source", "attachment"]
    media_type: Literal["audio", "image", "video", "text"]
    filename: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    content_type: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    size_bytes: int = Field(gt=0)
    sha256: Sha256 | None = None
    session_id: int | None = None
    fragment_id: int | None = None

    @model_validator(mode="after")
    def validate_purpose_and_binding(self):
        if self.purpose == "recording":
            valid = (
                self.media_type == "audio"
                and self.session_id is not None
                and self.fragment_id is None
            )
        elif self.purpose == "source":
            valid = (
                self.media_type in {"text", "video"}
                and self.session_id is not None
                and self.fragment_id is None
            )
        else:
            valid = (
                self.media_type in {"audio", "image", "video"}
                and ((self.fragment_id is None) != (self.session_id is None))
            )
        if not valid:
            raise ValueError("purpose, media_type, and session/fragment binding are inconsistent")
        return self


class UploadAuthorizationResponse(BaseModel):
    reused: bool
    asset_id: int
    upload_method: Literal["PUT"] | None = None
    upload_url: str | None = None
    required_headers: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime | None = None


class UploadCompleteRequest(BaseModel):
    # 在接口层拒绝超出对应 PostgreSQL 字段范围的数值。
    duration_ms: int | None = Field(default=None, ge=0, le=PG_BIGINT_MAX)
    sample_rate_hz: int | None = Field(default=None, gt=0, le=PG_INTEGER_MAX)


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


class FragmentCreate(NarrationInput):
    # 原录音客户端继续提交 audio_asset_id。
    audio_asset_id: int | None = None
    source_type: Literal["text", "audio", "video"] = "audio"
    # 无原声的 App 转写通过文字入口保存并标识来源。
    source_origin: Literal["app_asr"] | None = None
    source_asset_id: int | None = None
    content_text: str | None = None
    transcript_raw: str | None = None
    recorded_at: datetime | None = None
    allow_external_processing: StrictBool = False
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def validate_source(self):
        if self.source_origin == "app_asr" and (
            self.source_type != "text" or not (self.content_text and self.content_text.strip())
            or self.source_asset_id is not None or self.transcript_raw is not None
        ):
            raise ValueError("App 本地转写需用 content_text 提交，且不关联原声")
        if self.audio_asset_id is not None:
            if self.source_asset_id is not None and self.source_asset_id != self.audio_asset_id:
                raise ValueError("audio_asset_id 与 source_asset_id 不可冲突")
            if self.source_type != "audio":
                raise ValueError("audio_asset_id 仅适用于 audio 类型")
        if (
            self.source_type == "audio"
            and self.audio_asset_id is None
            and self.source_asset_id is None
        ):
            raise ValueError("audio 类型必须提供 source_asset_id")
        if self.source_type == "video" and self.source_asset_id is None:
            raise ValueError("video 类型必须提供 source_asset_id")
        if self.transcript_segments and (
            self.source_type == "text" or not self.transcript_raw or not self.transcript_raw.strip()
        ):
            raise ValueError("转写时间定位需要音频或视频及非空原始转写")
        if (
            self.source_type == "text"
            and not (self.content_text and self.content_text.strip())
            and self.source_asset_id is None
        ):
            raise ValueError("text 类型必须提供 content_text 或 source_asset_id")
        return self


class FragmentPatch(BaseModel):
    transcript_edited: str | None


class FragmentRawTranscriptUpdate(NarrationInput):
    model_config = ConfigDict(extra="forbid")

    transcript_raw: str
    allow_external_processing: StrictBool = False
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list, max_length=10_000)

    @field_validator("transcript_raw")
    @classmethod
    def validate_original_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("原始转写必须有正文且不能含 NUL 字符")
        # 保留原始空格和换行，抽取引用的位置以提交文本为准。
        return value


class AttachmentInput(BaseModel):
    asset_id: int
    caption: Annotated[str, StringConstraints(max_length=200)] | None = None
    sort_order: int = Field(default=0, ge=0, le=PG_INTEGER_MAX)


class AttachmentBatchCreate(BaseModel):
    items: list[AttachmentInput] = Field(min_length=1, max_length=9)


class AttachmentPatch(BaseModel):
    caption: Annotated[str, StringConstraints(max_length=200)] | None = None
    sort_order: int | None = Field(default=None, ge=0, le=PG_INTEGER_MAX)

    @model_validator(mode="after")
    def validate_sort_order(self):
        if "sort_order" in self.model_fields_set and self.sort_order is None:
            raise ValueError("sort_order 不能为 null")
        return self


class AttachmentResponse(BaseModel):
    id: int
    session_id: int
    fragment_id: int | None
    contributor_id: int
    asset: MediaAssetResponse
    caption: str | None
    sort_order: int
    created_at: datetime


class FragmentResponse(BaseModel):
    id: int
    session_id: int
    sequence_no: int
    contributor_id: int
    source_type: Literal["text", "audio", "video"]
    source_asset: MediaAssetResponse | None = None
    content_text: str | None = None
    transcript_raw: str | None
    transcript_edited: str | None
    recorded_at: datetime
    attachments: list[AttachmentResponse] = Field(default_factory=list)


class FragmentCreatedResponse(FragmentResponse):
    extraction_job: ExtractionJobResponse | None = None
    extraction_skip_reason: str | None = None


class FragmentPage(PageResponse):
    items: list[FragmentResponse]


class SessionDetail(SessionSummary):
    fragments: list[FragmentResponse]


class DeleteResponse(BaseModel):
    id: int
    deleted: bool
