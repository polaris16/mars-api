"""[新增] 保存事件成员、来源版本和同一事件确认依据。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, String, text

from app.models.base import AuditFields, Base

ACTIVE_MEMBER = "is_del = false AND status = 'active'"


class MemoryGraphEventElement(AuditFields, Base):
    __tablename__ = "t_memory_graph_event_element"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'superseded')", name="ck_memory_graph_member_status"
        ),
        CheckConstraint(
            "element_anchor_key ~ '^[0-9a-f]{64}$'", name="ck_memory_graph_member_anchor_key"
        ),
        CheckConstraint(
            "identity_evidence_fingerprint IS NULL "
            "OR identity_evidence_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_memory_graph_member_identity_fingerprint",
        ),
        CheckConstraint(
            "(membership_basis = 'source' AND identity_group_id IS NULL "
            "AND identity_answer_id IS NULL AND identity_evidence_fingerprint IS NULL) OR "
            "(membership_basis = 'confirmed_same_event' AND identity_group_id IS NOT NULL "
            "AND identity_answer_id IS NOT NULL AND identity_evidence_fingerprint IS NOT NULL)",
            name="ck_memory_graph_member_identity_basis",
        ),
        Index(
            "uq_memory_graph_member_active_element",
            "element_id",
            unique=True,
            postgresql_where=text(ACTIVE_MEMBER),
        ),
        Index(
            "idx_memory_graph_member_active_event",
            "event_id",
            "id",
            postgresql_where=text(ACTIVE_MEMBER),
        ),
        Index(
            "idx_memory_graph_member_active_source",
            "source_revision_id",
            postgresql_where=text(ACTIVE_MEMBER),
        ),
        Index(
            "idx_memory_graph_member_active_draft",
            "draft_version_id",
            postgresql_where=text(ACTIVE_MEMBER),
        ),
        Index(
            "idx_memory_graph_member_active_identity",
            "identity_group_id",
            postgresql_where=text(ACTIVE_MEMBER),
        ),
        Index("idx_memory_graph_member_anchor_history", "element_anchor_key", "id"),
        {"comment": "图谱事件成员、来源版本和归组依据"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="图谱事件成员主键 ID")
    event_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_graph_event.id", name="fk_memory_graph_member_event", ondelete="RESTRICT"
        ),
        nullable=False,
        comment="归入的图谱事件 ID",
    )
    element_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_element.id", name="fk_memory_graph_member_element", ondelete="RESTRICT"
        ),
        nullable=False,
        comment="本次发布引用的记忆元素 ID",
    )
    source_revision_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_source_revision.id",
            name="fk_memory_graph_member_source",
            ondelete="RESTRICT",
        ),
        nullable=False,
        comment="支撑该成员的原文版本 ID",
    )
    draft_version_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_draft_version.id", name="fk_memory_graph_member_draft", ondelete="RESTRICT"
        ),
        nullable=False,
        comment="元素绑定的整理稿版本 ID",
    )
    element_anchor_key = Column(
        String(64), nullable=False, comment="成员的语义锚点 SHA-256，用于重跑及拆分溯源"
    )
    membership_basis = Column(
        String(24),
        nullable=False,
        comment="source 独立证据归属，confirmed_same_event 用户确认同一事件",
    )
    identity_group_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_conflict_group.id",
            name="fk_memory_graph_member_identity",
            ondelete="RESTRICT",
        ),
        comment="确认属于同一事件的核对组 ID",
    )
    identity_answer_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_review_answer.id", name="fk_memory_graph_member_answer", ondelete="RESTRICT"
        ),
        comment="发布时采用的同一事件确认回答 ID",
    )
    identity_evidence_fingerprint = Column(
        String(64), comment="发布时归组证据的 SHA-256，用于核对裁定是否仍适用"
    )
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'active'"),
        comment="active 当前成员，superseded 已被替代或失效",
    )
    published_by_job_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_validation_job.id", name="fk_memory_graph_member_job", ondelete="RESTRICT"
        ),
        nullable=False,
        comment="首次发布此版事件成员的复验任务 ID",
    )
