"""ORM model for detected conflicts between structured memory elements."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base


class MemoryConflict(Base):
    __tablename__ = "t_memory_conflict"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_memory_conflict_pkey"),
        CheckConstraint(
            "element_a_id < element_b_id", name="ck_t_memory_conflict_distinct_ordered_elements"
        ),
        CheckConstraint(
            "status IN ('pending', 'awaiting_user', 'resolved', 'cannot_remember', 'dismissed')",
            name="ck_t_memory_conflict_status",
        ),
        CheckConstraint(
            "jsonb_typeof(conflict_payload) = 'object'", name="ck_t_memory_conflict_payload_object"
        ),
        CheckConstraint(
            "resolution_payload IS NULL OR jsonb_typeof(resolution_payload) = 'object'",
            name="ck_t_memory_conflict_resolution_object",
        ),
        Index(
            "uq_t_memory_conflict_active_elements_type",
            "element_a_id",
            "element_b_id",
            "conflict_type",
            unique=True,
            postgresql_where=text("is_del = false"),
        ),
        Index("idx_t_memory_conflict_status_created", "status", "created_at"),
        Index("idx_t_memory_conflict_job", "detected_by_job_id"),
        # [新增] 成对冲突沿组核对，并保留原有独立记录。
        CheckConstraint(
            "conflict_group_id IS NULL OR validation_job_id IS NOT NULL",
            name="ck_memory_conflict_group_job",
        ),
        Index("idx_memory_conflict_group", "conflict_group_id", "id"),
        Index("idx_memory_conflict_validation_job", "validation_job_id"),
        {"comment": "记忆元素间的冲突及用户确认结果"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    element_a_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_element.id", ondelete="CASCADE", name="fk_t_memory_conflict_element_a"
        ),
        nullable=False,
        comment="冲突中的第一个记忆元素 ID，按 ID 升序保存",
    )
    element_b_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_element.id", ondelete="CASCADE", name="fk_t_memory_conflict_element_b"
        ),
        nullable=False,
        comment="冲突中的第二个记忆元素 ID",
    )
    detected_by_job_id = Column(
        BigInteger,
        ForeignKey("t_ai_processing_job.id", ondelete="SET NULL", name="fk_t_memory_conflict_job"),
        nullable=True,
        comment="发现冲突的 AI 任务 ID",
    )
    # [新增] 第四阶段的新成对记录关联冲突组和发布结果的任务。
    conflict_group_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_conflict_group.id", ondelete="RESTRICT", name="fk_memory_conflict_group"
        ),
        comment="所属冲突组 ID，历史记录可空",
    )
    validation_job_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_validation_job.id", ondelete="RESTRICT",
            name="fk_memory_conflict_validation_job",
        ),
        comment="发布本次成对冲突的复验任务 ID",
    )
    conflict_type = Column(String(40), nullable=False, comment="冲突涉及的属性或事实类型")
    description = Column(Text, nullable=False, comment="冲突的内容及差异说明")
    question_text = Column(Text, nullable=True, comment="发给用户的核对问题")
    conflict_payload = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="检测冲突时保留的来源证据，JSON 对象",
    )
    status = Column(
        String(24),
        nullable=False,
        server_default=text("'pending'"),
        comment="处理状态：pending 待处理、awaiting_user 待确认、resolved 已解决、cannot_remember 记不清、dismissed 已忽略",
    )
    user_response = Column(Text, nullable=True, comment="用户对冲突的原始回答")
    resolution_payload = Column(JSONB, nullable=True, comment="结构化的确认或处理结果，JSON 对象")
    resolved_by_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="SET NULL", name="fk_t_memory_conflict_resolver"),
        nullable=True,
        comment="确认或处理冲突的用户 ID",
    )
    resolved_at = Column(DateTime(timezone=True), nullable=True, comment="记录最终处理结果的时间")
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
