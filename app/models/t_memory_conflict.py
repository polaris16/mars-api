"""ORM model for detected conflicts between structured memory elements."""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, PrimaryKeyConstraint, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base


class MemoryConflict(Base):
    __tablename__ = "t_memory_conflict"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_memory_conflict_pkey"),
        CheckConstraint("element_a_id < element_b_id", name="ck_t_memory_conflict_distinct_ordered_elements"),
        CheckConstraint("status IN ('pending', 'awaiting_user', 'resolved', 'cannot_remember', 'dismissed')", name="ck_t_memory_conflict_status"),
        CheckConstraint("jsonb_typeof(conflict_payload) = 'object'", name="ck_t_memory_conflict_payload_object"),
        CheckConstraint("resolution_payload IS NULL OR jsonb_typeof(resolution_payload) = 'object'", name="ck_t_memory_conflict_resolution_object"),
        Index("uq_t_memory_conflict_active_elements_type", "element_a_id", "element_b_id", "conflict_type", unique=True, postgresql_where=text("is_del = false")),
        Index("idx_t_memory_conflict_status_created", "status", "created_at"),
        Index("idx_t_memory_conflict_job", "detected_by_job_id"),
        {"comment": "Detected conflicts between structured memory elements"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="Id")
    element_a_id = Column(BigInteger, ForeignKey("t_memory_element.id", ondelete="CASCADE", name="fk_t_memory_conflict_element_a"), nullable=False, comment="Element a id")
    element_b_id = Column(BigInteger, ForeignKey("t_memory_element.id", ondelete="CASCADE", name="fk_t_memory_conflict_element_b"), nullable=False, comment="Element b id")
    detected_by_job_id = Column(BigInteger, ForeignKey("t_ai_processing_job.id", ondelete="SET NULL", name="fk_t_memory_conflict_job"), nullable=True, comment="Detected by job id")
    conflict_type = Column(String(40), nullable=False, comment="Conflict type")
    description = Column(Text, nullable=False, comment="Description")
    question_text = Column(Text, nullable=True, comment="Question text")
    conflict_payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), comment="Conflict payload")
    status = Column(String(24), nullable=False, server_default=text("'pending'"), comment="Status")
    user_response = Column(Text, nullable=True, comment="User response")
    resolution_payload = Column(JSONB, nullable=True, comment="Resolution payload")
    resolved_by_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="SET NULL", name="fk_t_memory_conflict_resolver"), nullable=True, comment="Resolved by user id")
    resolved_at = Column(DateTime(timezone=True), nullable=True, comment="Resolved at")
    created_by = Column(BigInteger, nullable=True, comment="Created by")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="Created at")
    updated_by = Column(BigInteger, nullable=True, comment="Updated by")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="Updated at")
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="Is del")
