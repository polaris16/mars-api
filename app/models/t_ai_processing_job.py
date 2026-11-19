"""ORM model for ai processing jobs."""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, PrimaryKeyConstraint, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base


class AIProcessingJob(Base):
    __tablename__ = "t_ai_processing_job"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_ai_processing_job_pkey"),
        CheckConstraint("status IN ('queued', 'processing', 'succeeded', 'failed', 'cancelled')", name="ck_t_ai_processing_job_status"),
        CheckConstraint("attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts", name="ck_t_ai_processing_job_attempts"),
        CheckConstraint("priority BETWEEN 0 AND 10", name="ck_t_ai_processing_job_priority"),
        CheckConstraint("jsonb_typeof(input_payload) = 'object'", name="ck_t_ai_processing_job_input_object"),
        CheckConstraint("result_payload IS NULL OR jsonb_typeof(result_payload) = 'object'", name="ck_t_ai_processing_job_result_object"),
        Index("uq_t_ai_processing_job_idempotency_active", "session_id", "idempotency_key", unique=True, postgresql_where=text("idempotency_key IS NOT NULL AND is_del = false")),
        Index("idx_t_ai_processing_job_queue", "status", "available_at", "priority"),
        Index("idx_t_ai_processing_job_fragment", "source_fragment_id", "created_at"),
        {"comment": "AI processing jobs"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="Id")
    session_id = Column(BigInteger, ForeignKey("t_memory_session.id", ondelete="CASCADE", name="fk_t_ai_processing_job_session"), nullable=False, comment="Session id")
    source_fragment_id = Column(BigInteger, ForeignKey("t_memory_fragment.id", ondelete="SET NULL", name="fk_t_ai_processing_job_fragment"), nullable=True, comment="Source fragment id")
    requested_by_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="SET NULL", name="fk_t_ai_processing_job_requester"), nullable=True, comment="Requested by user id")
    job_type = Column(String(50), nullable=False, comment="Job type")
    status = Column(String(20), nullable=False, server_default=text("'queued'"), comment="Status")
    priority = Column(SmallInteger, nullable=False, server_default=text("0"), comment="Priority")
    input_payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), comment="Input payload")
    result_payload = Column(JSONB, nullable=True, comment="Result payload")
    attempt_count = Column(Integer, nullable=False, server_default=text("0"), comment="Attempt count")
    max_attempts = Column(Integer, nullable=False, server_default=text("3"), comment="Max attempts")
    available_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="Available at")
    started_at = Column(DateTime(timezone=True), nullable=True, comment="Started at")
    finished_at = Column(DateTime(timezone=True), nullable=True, comment="Finished at")
    worker_id = Column(String(100), nullable=True, comment="Worker id")
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, comment="Lease expires at")
    idempotency_key = Column(String(120), nullable=True, comment="Idempotency key")
    error_code = Column(String(100), nullable=True, comment="Error code")
    error_message = Column(Text, nullable=True, comment="Error message")
    created_by = Column(BigInteger, nullable=True, comment="Created by")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="Created at")
    updated_by = Column(BigInteger, nullable=True, comment="Updated by")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="Updated at")
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="Is del")
