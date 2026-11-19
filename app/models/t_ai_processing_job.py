"""ORM model for ai processing jobs."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base


class AIProcessingJob(Base):
    __tablename__ = "t_ai_processing_job"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_ai_processing_job_pkey"),
        CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed', 'cancelled')",
            name="ck_t_ai_processing_job_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_t_ai_processing_job_attempts",
        ),
        CheckConstraint("priority BETWEEN 0 AND 10", name="ck_t_ai_processing_job_priority"),
        CheckConstraint(
            "jsonb_typeof(input_payload) = 'object'", name="ck_t_ai_processing_job_input_object"
        ),
        CheckConstraint(
            "result_payload IS NULL OR jsonb_typeof(result_payload) = 'object'",
            name="ck_t_ai_processing_job_result_object",
        ),
        Index(
            "uq_t_ai_processing_job_idempotency_active",
            "session_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND is_del = false"),
        ),
        Index("idx_t_ai_processing_job_queue", "status", "available_at", "priority"),
        Index("idx_t_ai_processing_job_fragment", "source_fragment_id", "created_at"),
        {"comment": "AI 素材处理任务"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    session_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_session.id", ondelete="CASCADE", name="fk_t_ai_processing_job_session"
        ),
        nullable=False,
        comment="所属记忆会话 ID",
    )
    source_fragment_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_fragment.id", ondelete="SET NULL", name="fk_t_ai_processing_job_fragment"
        ),
        nullable=True,
        comment="触发任务的来源片段 ID",
    )
    requested_by_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="SET NULL", name="fk_t_ai_processing_job_requester"),
        nullable=True,
        comment="提交处理任务的用户 ID",
    )
    job_type = Column(
        String(50), nullable=False, comment="任务类型，如 extract_memory_elements 表示抽取记忆元素"
    )
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'queued'"),
        comment="任务状态：queued 待执行、processing 执行中、succeeded 成功、failed 失败、cancelled 取消",
    )
    priority = Column(
        SmallInteger,
        nullable=False,
        server_default=text("0"),
        comment="任务优先级，取值 0 至 10，值越大越优先",
    )
    input_payload = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="任务输入引用、内容指纹和处理版本，JSON 对象",
    )
    result_payload = Column(JSONB, nullable=True, comment="任务统计、原文定位和失败记录，JSON 对象")
    attempt_count = Column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="已领取的执行次数，包含首次同步尝试",
    )
    max_attempts = Column(
        Integer, nullable=False, server_default=text("3"), comment="任务允许尝试的总次数"
    )
    available_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="任务最早可领取的时间",
    )
    started_at = Column(DateTime(timezone=True), nullable=True, comment="首次领取任务的时间")
    finished_at = Column(DateTime(timezone=True), nullable=True, comment="任务达到终态的时间")
    worker_id = Column(String(100), nullable=True, comment="本次领取的租约令牌，用于核对提交者")
    lease_expires_at = Column(
        DateTime(timezone=True), nullable=True, comment="当前执行租约到期时间"
    )
    idempotency_key = Column(String(120), nullable=True, comment="同一会话内的任务幂等键")
    error_code = Column(String(100), nullable=True, comment="最近一次失败或取消的错误码")
    error_message = Column(Text, nullable=True, comment="可选的安全错误说明")
    created_by = Column(BigInteger, nullable=True, comment="创建人 ID")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )
    updated_by = Column(BigInteger, nullable=True, comment="更新人 ID")
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="是否软删除")
