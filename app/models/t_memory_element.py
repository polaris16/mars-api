"""ORM model for structured memory elements extracted from source fragments."""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, PrimaryKeyConstraint, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base


class MemoryElement(Base):
    __tablename__ = "t_memory_element"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_memory_element_pkey"),
        Index("uq_t_memory_element_fragment_sequence_active", "source_fragment_id", "sequence_no", unique=True, postgresql_where=text("is_del = false")),
        CheckConstraint("element_type IN ('event', 'fact', 'life_stage', 'other')", name="ck_t_memory_element_type"),
        CheckConstraint("origin IN ('extracted', 'fallback')", name="ck_t_memory_element_origin"),
        Index(
            "uq_t_memory_element_active_fallback", "source_fragment_id", unique=True,
            postgresql_where=text("origin = 'fallback' AND is_del = false"),
            sqlite_where=text("origin = 'fallback' AND is_del = false"),
        ),
        CheckConstraint("review_status IN ('pending', 'confirmed', 'corrected', 'cannot_remember', 'rejected')", name="ck_t_memory_element_review_status"),
        CheckConstraint("time_start IS NULL OR time_end IS NULL OR time_start <= time_end", name="ck_t_memory_element_time_range"),
        CheckConstraint("overall_confidence IS NULL OR overall_confidence BETWEEN 0 AND 1", name="ck_t_memory_element_confidence"),
        CheckConstraint("jsonb_typeof(attribute_confidence) = 'object'", name="ck_t_memory_element_confidence_object"),
        Index("idx_t_memory_element_session_status", "session_id", "review_status", "id"),
        Index("idx_t_memory_element_job", "processing_job_id"),
        {"comment": "Structured memory elements extracted from source fragments"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="Id")
    session_id = Column(BigInteger, ForeignKey("t_memory_session.id", ondelete="CASCADE", name="fk_t_memory_element_session"), nullable=False, comment="Session id")
    source_fragment_id = Column(BigInteger, ForeignKey("t_memory_fragment.id", ondelete="CASCADE", name="fk_t_memory_element_fragment"), nullable=False, comment="Source fragment id")
    processing_job_id = Column(BigInteger, ForeignKey("t_ai_processing_job.id", ondelete="SET NULL", name="fk_t_memory_element_job"), nullable=True, comment="Processing job id")
    sequence_no = Column(Integer, nullable=False, comment="Sequence no")
    element_type = Column(String(30), nullable=False, server_default=text("'event'"), comment="Element type")
    origin = Column(String(20), nullable=False, server_default=text("'extracted'"), comment="生成来源：extracted 正常抽取，fallback 原文兜底")
    summary = Column(Text, nullable=False, comment="Summary")
    source_quote = Column(Text, nullable=True, comment="Source quote")
    exact_at = Column(DateTime(timezone=True), nullable=True, comment="Exact at")
    time_start = Column(Date, nullable=True, comment="Time start")
    time_end = Column(Date, nullable=True, comment="Time end")
    time_description = Column(Text, nullable=True, comment="Time description")
    location_description = Column(Text, nullable=True, comment="Location description")
    attribute_confidence = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), comment="Attribute confidence")
    overall_confidence = Column(Numeric(5, 4), nullable=True, comment="Overall confidence")
    review_status = Column(String(24), nullable=False, server_default=text("'pending'"), comment="Review status")
    reviewed_by_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="SET NULL", name="fk_t_memory_element_reviewer"), nullable=True, comment="Reviewed by user id")
    reviewed_at = Column(DateTime(timezone=True), nullable=True, comment="Reviewed at")
    created_by = Column(BigInteger, nullable=True, comment="Created by")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="Created at")
    updated_by = Column(BigInteger, nullable=True, comment="Updated by")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="Updated at")
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="Is del")
