"""[新增] 所有者核对问题、提交回答及跟踪复验任务。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.memory_extraction import Id

QuestionStatus = Literal["pending", "answered", "skipped", "superseded"]
ReviewAction = Literal["confirm", "correct", "cannot_remember", "skip"]
Revision = Annotated[int, Field(strict=True, gt=0, le=2**31 - 1)]
ReviewId = Annotated[Id, Field(strict=True)]


class ReopenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: Revision


class ReviewAnswerCreate(ReopenQuestion):
    action: ReviewAction
    selected_option_id: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = (
        None
    )
    answer_text: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
        | None
    ) = None

    @model_validator(mode="after")
    def check_payload(self):
        if (self.action == "confirm") != (self.selected_option_id is not None):
            raise ValueError("仅确认动作需要选项 ID")
        if (self.action == "correct") != (self.answer_text is not None):
            raise ValueError("仅纠正动作需要补述文字")
        for value in (self.selected_option_id, self.answer_text):
            if value is not None and (not value.strip() or "\x00" in value):
                raise ValueError("提交内容包含无效字符")
        return self


class ValidationJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["verify_element", "rebuild", "retry"]
    element_id: ReviewId | None = None
    retry_of_job_id: ReviewId | None = None

    @model_validator(mode="after")
    def check_target(self):
        if (self.action == "verify_element") != (self.element_id is not None):
            raise ValueError("仅单元素核验需要元素 ID")
        if (self.action == "retry") != (self.retry_of_job_id is not None):
            raise ValueError("仅重试动作需要原任务 ID")
        return self


class ReviewEvidenceResponse(BaseModel):
    id: int
    session_id: int | None = None
    element_id: int | None = None
    source_revision_id: int | None = None
    draft_version_id: int | None = None
    answer_id: int | None = None
    excerpt: str | None = None


class ReviewOption(BaseModel):
    id: str
    label: str
    evidence_ids: list[int]


class ReviewQuestionResponse(BaseModel):
    id: int
    revision: int
    conflict_group_id: int
    kind: Literal["clarify_event", "confirm_value", "follow_up"]
    status: QuestionStatus
    question_text: str
    options: list[ReviewOption]
    evidence: list[ReviewEvidenceResponse]


class ReviewQuestionPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[ReviewQuestionResponse]


class ValidationJobAccepted(BaseModel):
    validation_job_id: int


class ReviewAnswerAccepted(ValidationJobAccepted):
    answer_id: int


class ValidationJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_type: Literal["verify_element", "review_memoir", "rebuild_memoir"]
    status: Literal["queued", "processing", "succeeded", "failed", "cancelled"]
    attempt_count: int
    max_attempts: int
    retry_of_job_id: int | None
    target_element_id: int | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result_payload: dict[str, int]
