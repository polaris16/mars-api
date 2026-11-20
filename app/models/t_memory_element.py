"""ORM model for structured memory elements extracted from source fragments."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base


class MemoryElement(Base):
    __tablename__ = "t_memory_element"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_memory_element_pkey"),
        Index(
            "uq_t_memory_element_fragment_sequence_active",
            "source_fragment_id",
            "sequence_no",
            unique=True,
            postgresql_where=text("is_del = false"),
        ),
        CheckConstraint(
            "element_type IN ('event', 'fact', 'life_stage', 'other')",
            name="ck_t_memory_element_type",
        ),
        CheckConstraint("origin IN ('extracted', 'fallback')", name="ck_t_memory_element_origin"),
        Index(
            "uq_t_memory_element_active_fallback",
            "source_fragment_id",
            unique=True,
            postgresql_where=text("origin = 'fallback' AND is_del = false"),
            sqlite_where=text("origin = 'fallback' AND is_del = false"),
        ),
        CheckConstraint(
            "review_status IN ('pending', 'confirmed', 'corrected', 'cannot_remember', 'rejected')",
            name="ck_t_memory_element_review_status",
        ),
        CheckConstraint(
            "time_start IS NULL OR time_end IS NULL OR time_start <= time_end",
            name="ck_t_memory_element_time_range",
        ),
        CheckConstraint(
            "overall_confidence IS NULL OR overall_confidence BETWEEN 0 AND 1",
            name="ck_t_memory_element_confidence",
        ),
        CheckConstraint(
            "jsonb_typeof(attribute_confidence) = 'object'",
            name="ck_t_memory_element_confidence_object",
        ),
        Index("idx_t_memory_element_session_status", "session_id", "review_status", "id"),
        Index("idx_t_memory_element_job", "processing_job_id"),
        Index("idx_memory_element_draft", "draft_version_id"),
        Index("idx_memory_element_verification", "session_id", "verification_status"),
        # [新增] 追溯当前系统验证状态由哪个任务发布。
        Index("idx_memory_element_validation_job", "last_validation_job_id"),
        CheckConstraint(
            "verification_status IN ('pending', 'verified', 'conflict')",
            name="ck_memory_element_verification",
        ),
        CheckConstraint(
            "draft_references IS NULL OR jsonb_typeof(draft_references) = 'array'",
            name="ck_memory_element_draft_references",
        ),
        {"comment": "结构化记忆元素及抽取失败保留的原文兜底"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    session_id = Column(
        BigInteger,
        ForeignKey("t_memory_session.id", ondelete="CASCADE", name="fk_t_memory_element_session"),
        nullable=False,
        comment="所属记忆会话 ID",
    )
    source_fragment_id = Column(
        BigInteger,
        ForeignKey("t_memory_fragment.id", ondelete="CASCADE", name="fk_t_memory_element_fragment"),
        nullable=False,
        comment="来源片段 ID",
    )
    processing_job_id = Column(
        BigInteger,
        ForeignKey("t_ai_processing_job.id", ondelete="SET NULL", name="fk_t_memory_element_job"),
        nullable=True,
        comment="产生该记忆元素的 AI 任务 ID",
    )
    sequence_no = Column(Integer, nullable=False, comment="同一来源片段内的元素顺序")
    draft_version_id = Column(
        BigInteger,
        ForeignKey("t_memory_draft_version.id", name="fk_memory_element_draft", ondelete="CASCADE"),
        comment="抽取时生效的整理稿版本 ID",
    )
    draft_references = Column(JSONB(none_as_null=True), comment="整理稿段落及段内引用位置")
    verification_status = Column(
        String(20), comment="系统验证状态，待核对、已验证或冲突，独立于人工核对"
    )
    # [新增] 验证原因与最后一次发布任务，旧元素可以保留空值。
    verification_reason = Column(String(40), comment="未通过系统核验的原因码，通过时为空")
    last_validation_job_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_validation_job.id", ondelete="RESTRICT",
            name="fk_memory_element_validation_job", use_alter=True,
        ),
        comment="最近发布验证状态的复验任务 ID",
    )
    element_type = Column(
        String(30),
        nullable=False,
        server_default=text("'event'"),
        comment="元素类型：event 事件、fact 事实、life_stage 人生阶段、other 其他",
    )
    origin = Column(
        String(20),
        nullable=False,
        server_default=text("'extracted'"),
        comment="生成来源：extracted 正常抽取，fallback 原文兜底",
    )
    summary = Column(Text, nullable=False, comment="记忆内容摘要，原文兜底时仅保存预览")
    source_quote = Column(Text, nullable=True, comment="可在来源文本中逐字核对的引句")
    exact_at = Column(
        DateTime(timezone=True), nullable=True, comment="有明确依据的精确时间，带时区"
    )
    time_start = Column(Date, nullable=True, comment="推定日期范围的起始日期")
    time_end = Column(Date, nullable=True, comment="推定日期范围的结束日期")
    time_description = Column(Text, nullable=True, comment="用户对时间的原始或模糊表述")
    location_description = Column(Text, nullable=True, comment="根据原文归纳的地点描述")
    attribute_confidence = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="按属性记录的置信度，JSON 对象",
    )
    overall_confidence = Column(
        Numeric(5, 4), nullable=True, comment="整个元素的模型置信度，取值 0 至 1"
    )
    review_status = Column(
        String(24),
        nullable=False,
        server_default=text("'pending'"),
        comment="核对状态：pending 待核对、confirmed 确认、corrected 已修正、cannot_remember 记不清、rejected 已拒绝",
    )
    reviewed_by_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="SET NULL", name="fk_t_memory_element_reviewer"),
        nullable=True,
        comment="核对记忆元素的用户 ID",
    )
    reviewed_at = Column(DateTime(timezone=True), nullable=True, comment="记录核对结论的时间")
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
