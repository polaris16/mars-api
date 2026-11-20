"""[新增] 记忆整理、来源定位和实体候选的请求模型。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator

from app.schemas.memory_extraction import Id, MemoryElementResponse

MAX_SOURCE_CHARS = 500_000
MAX_DRAFT_PARAGRAPHS = 300
Offset = Annotated[int, Field(strict=True, ge=0, le=MAX_SOURCE_CHARS)]
Milliseconds = Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
NarrativeText = Annotated[str, StringConstraints(min_length=1, max_length=MAX_SOURCE_CHARS)]
EntityName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
EntityType = Literal["person", "place", "organization", "time_period"]
ProcessingId = Annotated[Id, Field(strict=True)]


class TextRange(BaseModel):
    """原文的 Unicode 字符位置，采用左闭右开区间。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_offset: Offset
    end_offset: Offset

    @model_validator(mode="after")
    def _check_offset_order(self) -> "TextRange":
        if self.end_offset <= self.start_offset:
            raise ValueError("字符区间的结束位置必须大于起始位置")
        return self


class AudioRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start_ms: Milliseconds
    end_ms: Milliseconds

    @model_validator(mode="after")
    def _check_time_order(self) -> "AudioRange":
        if self.end_ms <= self.start_ms:
            raise ValueError("原声区间的结束时间必须大于起始时间")
        return self


class TranscriptSegment(TextRange, AudioRange):
    """ASR 原文区间与原声区间的对应关系。"""


class NarratorSpan(TextRange):
    """原文字符区间内的实际叙述者，空值表示此段归属尚未确认。"""

    narrator_user_id: ProcessingId | None


class NarrationInput(BaseModel):
    narrator_user_id: ProcessingId | None = None
    narrator_spans: list[NarratorSpan] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def _check_narrator_spans(self) -> "NarrationInput":
        if any(
            following.start_offset < previous.end_offset
            for previous, following in zip(self.narrator_spans, self.narrator_spans[1:])
        ):
            raise ValueError("叙述者区间必须按原文位置排序且不得重叠")
        return self


class DraftParagraphInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    text: NarrativeText


class DraftVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_revision_id: ProcessingId
    base_version_id: ProcessingId | None
    paragraphs: list[DraftParagraphInput] = Field(min_length=1, max_length=MAX_DRAFT_PARAGRAPHS)

    @model_validator(mode="after")
    def _check_paragraphs(self) -> "DraftVersionCreate":
        identifiers = [item.id for item in self.paragraphs if item.id is not None]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("整理稿中不能重复使用同一段落 ID")
        if self.base_version_id is None and identifiers:
            raise ValueError("首版整理稿不能引用旧段落 ID")
        if any(not item.text.strip() or "\x00" in item.text for item in self.paragraphs):
            raise ValueError("段落必须包含有效正文")
        if sum(len(item.text) for item in self.paragraphs) > MAX_SOURCE_CHARS:
            raise ValueError("整理稿正文超出长度上限")
        return self


class ProcessingJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Literal["asr", "organization"]
    retry_of_job_id: ProcessingId | None = None
    allow_external_processing: StrictBool = False


class CandidateResolutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: Annotated[int, Field(strict=True, gt=0, le=2**31 - 1)]
    action: Literal["link_existing", "create_new", "reject_match"]
    entity_id: ProcessingId | None = None
    canonical_name: EntityName | None = None
    # [新增] 仅在同一引句有多个可核对位置时选择已登记的位置。
    evidence_location_index: Annotated[int, Field(strict=True, ge=0)] | None = None

    @model_validator(mode="after")
    def _check_target(self) -> "CandidateResolutionCreate":
        if self.action == "create_new":
            if self.entity_id is not None or not self.canonical_name or "\x00" in self.canonical_name:
                raise ValueError("新建实体必须提供有效名称")
        elif self.entity_id is None or self.canonical_name is not None:
            raise ValueError("关联或排除时必须提供目标实体 ID")
        return self


class OrganizedParagraph(BaseModel):
    """模型提供段落正文与可在原文中逐字核对的引句。"""

    model_config = ConfigDict(extra="forbid")

    text: NarrativeText
    source_quotes: list[NarrativeText] = Field(min_length=1, max_length=30)


class OrganizedNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paragraphs: list[OrganizedParagraph] = Field(min_length=1, max_length=MAX_DRAFT_PARAGRAPHS)


class Pagination(BaseModel):
    page: int
    page_size: int
    total: int


class StageStatus(BaseModel):
    status: Literal[
        "queued", "processing", "succeeded", "failed", "cancelled", "not_started", "skipped", "blocked"
    ]
    job_id: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    next_retry_at: datetime | None = None
    error_code: str | None = None
    reason_code: str | None = None


class ExtractionStageStatus(StageStatus):
    elements: list[MemoryElementResponse] | None = None
    fallback_element_id: int | None = None


class ProcessingResponse(BaseModel):
    fragment_id: int
    source_revision_id: int | None
    source_kind: Literal["content_text", "text_file", "app_asr", "server_asr"] | None = None
    draft_version_id: int | None
    upload_processed_at: datetime | None
    asr: StageStatus
    organization: StageStatus
    extraction: ExtractionStageStatus


class ProcessingJobResponse(BaseModel):
    job_id: int
    stage: Literal["asr", "organization"]
    status: str


class DraftVersionSummary(BaseModel):
    id: int
    source_revision_id: int
    base_version_id: int | None
    origin: Literal["model", "owner"]
    created_at: datetime


class DraftParagraphResponse(BaseModel):
    id: UUID
    text: str
    owner_edited: bool
    source_references: list[dict]


class DraftVersionResponse(DraftVersionSummary):
    paragraphs: list[DraftParagraphResponse]


class DraftVersionPage(Pagination):
    items: list[DraftVersionSummary]
    active_version_id: int | None


class EntityLabel(BaseModel):
    id: int
    display_name: str


class EntitySummary(EntityLabel):
    entity_type: EntityType


class EntityPage(Pagination):
    items: list[EntitySummary]


class EntityDetail(EntitySummary):
    aliases: list[str]
    evidence: list[dict]


class EntityCandidateResponse(BaseModel):
    id: int
    revision: int
    mention_text: str
    element_id: int
    entity_type: EntityType
    narrator_user_id: int | None = None
    evidence: dict
    candidate_entities: list[EntityLabel]


class EntityCandidatePage(Pagination):
    items: list[EntityCandidateResponse]


class CandidateResolutionResponse(BaseModel):
    id: int
    candidate_id: int
    action: Literal["link_existing", "create_new", "reject_match"]
    entity_id: int
    revision: int
    status: Literal["pending", "resolved"]
    entities: list[dict]
