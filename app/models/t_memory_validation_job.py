"""[新增] 按所有者管理跨会话核验、重构和重试任务。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import AuditFields, Base


class MemoryValidationJob(AuditFields, Base):
    __tablename__ = "t_memory_validation_job"
    __table_args__ = (
        CheckConstraint(
            "job_type IN ('verify_element', 'review_memoir', 'rebuild_memoir')",
            name="ck_memory_validation_job_type",
        ),
        CheckConstraint(
            "(job_type = 'verify_element') = (target_element_id IS NOT NULL)",
            name="ck_memory_validation_job_target",
        ),
        CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed', 'cancelled')",
            name="ck_memory_validation_job_status",
        ),
        CheckConstraint(
            "max_attempts > 0 AND attempt_count BETWEEN 0 AND max_attempts",
            name="ck_memory_validation_job_attempts",
        ),
        CheckConstraint(
            "(status = 'processing') = (worker_token IS NOT NULL) AND "
            "(status = 'processing') = (lease_expires_at IS NOT NULL)",
            name="ck_memory_validation_job_lease",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed', 'cancelled')) = (finished_at IS NOT NULL)",
            name="ck_memory_validation_job_finished",
        ),
        CheckConstraint(
            "jsonb_typeof(input_payload) = 'object'", name="ck_memory_validation_job_input"
        ),
        CheckConstraint(
            "jsonb_typeof(result_payload) = 'object'", name="ck_memory_validation_job_result"
        ),
        UniqueConstraint(
            "owner_user_id", "idempotency_key", name="uq_memory_validation_job_request"
        ),
        Index(
            "uq_memory_validation_job_processing",
            "owner_user_id",
            unique=True,
            postgresql_where=text("is_del = false AND status = 'processing'"),
        ),
        Index(
            "idx_memory_validation_job_queue",
            "available_at",
            "id",
            postgresql_where=text("is_del = false AND status = 'queued'"),
        ),
        Index(
            "idx_memory_validation_job_lease",
            "lease_expires_at",
            "id",
            postgresql_where=text("is_del = false AND status = 'processing'"),
        ),
        Index("idx_memory_validation_job_owner_status", "owner_user_id", "status", "id"),
        Index("idx_memory_validation_job_target", "target_element_id"),
        Index("idx_memory_validation_job_retry", "retry_of_job_id"),
        {"comment": "回忆录范围内的记忆验证、复验和重构任务"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    owner_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        nullable=False,
        comment="任务作用范围的回忆录所有者 ID",
    )
    requested_by_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        comment="任务发起人 ID，系统触发时为空",
    )
    job_type = Column(String(24), nullable=False, comment="单元素核验、回忆录复验或重构")
    target_element_id = Column(
        BigInteger,
        ForeignKey("t_memory_element.id", ondelete="RESTRICT"),
        comment="单元素任务需要核验的元素 ID",
    )
    retry_of_job_id = Column(
        BigInteger,
        ForeignKey("t_memory_validation_job.id", ondelete="RESTRICT"),
        comment="手动重试关联的前一任务 ID",
    )
    status = Column(
        String(24),
        nullable=False,
        server_default=text("'queued'"),
        comment="排队、执行中、成功、失败或取消",
    )
    attempt_count = Column(
        Integer, nullable=False, server_default=text("0"), comment="已领取执行的次数，含租约超时"
    )
    max_attempts = Column(
        Integer, nullable=False, server_default=text("3"), comment="单次任务的最大尝试次数"
    )
    available_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="允许首次领取或重试的时间",
    )
    started_at = Column(DateTime(timezone=True), comment="最近一次领取时间")
    finished_at = Column(DateTime(timezone=True), comment="任务进入最终状态的时间")
    worker_token = Column(Uuid, comment="当前领取者用于发布结果的租约令牌")
    lease_expires_at = Column(DateTime(timezone=True), comment="当前执行租约到期时间")
    idempotency_key = Column(String(120), nullable=False, comment="同一所有者下的触发去重键")
    input_fingerprint = Column(String(64), comment="开始核验时输入版本和权限快照的 SHA-256")
    input_payload = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="任务触发引用和实际核对的输入版本清单",
    )
    result_payload = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="已验证、冲突及待回答项的统计和引用",
    )
    error_code = Column(String(100), comment="任务失败、租约耗尽或取消的原因码")
