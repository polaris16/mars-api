"""记忆抽取请求、结果及模型输出。"""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

ElementType = Literal["event", "fact", "life_stage", "other"]
ElementOrigin = Literal["extracted", "fallback"]
ReviewStatus = Literal["pending", "confirmed", "corrected", "cannot_remember", "rejected"]
SourceKind = Literal["content_text", "transcript_raw", "text_file", "draft_version"]
VerificationStatus = Literal["pending", "verified", "conflict"]
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Id = Annotated[int, Field(gt=0, le=2**63 - 1)]


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retry_of_job_id: Id | None = None
    allow_external_processing: StrictBool = False


class ExtractionJobResponse(BaseModel):
    id: int
    session_id: int
    source_fragment_id: int
    status: Literal["queued", "processing", "succeeded", "failed", "cancelled"]
    attempt_count: int
    max_attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    next_retry_at: datetime | None
    element_count: int | None
    error_code: str | None
    retry_of_job_id: int | None
    fallback_element_id: int | None


class ExtractionJobPage(BaseModel):
    items: list[ExtractionJobResponse]
    page: int
    page_size: int
    total: int


class MemoryElementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    source_fragment_id: int
    processing_job_id: int | None
    sequence_no: int
    element_type: ElementType
    origin: ElementOrigin
    summary: str
    exact_at: datetime | None
    time_start: date | None
    time_end: date | None
    time_description: str | None
    location_description: str | None
    overall_confidence: Confidence | None
    attribute_confidence: dict[str, Confidence]
    review_status: ReviewStatus
    draft_version_id: int | None = None
    verification_status: VerificationStatus | None = None
    entities: list[dict] = Field(default_factory=list)


class MemoryElementPage(BaseModel):
    items: list[MemoryElementResponse]
    page: int
    page_size: int
    total: int


class MemoryProvenanceResponse(BaseModel):
    element_id: int
    source_fragment_id: int
    processing_job_id: int | None
    source_kind: SourceKind | None
    source_asset_id: int | None
    source_quote: str | None
    start_offset: int | None = None
    end_offset: int | None = None
    source_text: str | None = None
    draft_references: list[dict] = Field(default_factory=list)


class ExtractedElement(BaseModel):
    """Untrusted model output. A service verifies quotations against the actual input."""

    model_config = ConfigDict(extra="forbid")

    element_type: ElementType
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    source_quote: str = Field(min_length=1, max_length=2000)
    exact_at: AwareDatetime | None = None
    time_start: date | None = None
    time_end: date | None = None
    time_description: str | None = Field(default=None, max_length=400)
    location_description: str | None = Field(default=None, max_length=400)
    overall_confidence: Confidence | None = None
    attribute_confidence: dict[str, Confidence] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.time_start and self.time_end and self.time_start > self.time_end:
            raise ValueError("time_start must be on or before time_end")
        strings = (
            self.summary,
            self.source_quote,
            self.time_description,
            self.location_description,
            *self.attribute_confidence,
        )
        if any(value and "\x00" in value for value in strings):
            raise ValueError("text values must not contain NUL")
        return self


class ExtractedElements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elements: list[ExtractedElement] = Field(max_length=30)


class EntityMention(BaseModel):
    """模型只提出称谓和身份线索，关联的判定由 Service 完成。"""

    model_config = ConfigDict(extra="forbid")

    entity_type: Literal["person", "place", "organization", "time_period"]
    mention_text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    relation_type: Literal["subject", "participant", "location", "mentioned", "other", "time_period"]
    identity_quote: str | None = Field(default=None, max_length=500)


class StructuredElement(ExtractedElement):
    entity_mentions: list[EntityMention] = Field(default_factory=list, max_length=20)


class StructuredElements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elements: list[StructuredElement] = Field(max_length=30)
