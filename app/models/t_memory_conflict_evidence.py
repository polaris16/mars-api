"""[新增] 保存冲突组的版本化证据及定位。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import AuditFields, Base


class MemoryConflictEvidence(AuditFields, Base):
    __tablename__ = "t_memory_conflict_evidence"
    __table_args__ = (
        CheckConstraint(
            "(element_id IS NOT NULL AND source_revision_id IS NOT NULL "
            "AND draft_version_id IS NOT NULL AND answer_id IS NULL) OR "
            "(element_id IS NULL AND source_revision_id IS NULL "
            "AND draft_version_id IS NULL AND answer_id IS NOT NULL)",
            name="ck_memory_conflict_evidence_source",
        ),
        CheckConstraint(
            "jsonb_typeof(normalized_value) = 'object'", name="ck_memory_conflict_evidence_value"
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_payload) = 'object'", name="ck_memory_conflict_evidence_payload"
        ),
        CheckConstraint(
            "status IN ('active', 'superseded')", name="ck_memory_conflict_evidence_status"
        ),
        Index(
            "uq_memory_conflict_evidence_active",
            "conflict_group_id",
            "evidence_key",
            unique=True,
            postgresql_where=text("is_del = false AND status = 'active'"),
        ),
        Index("idx_memory_conflict_evidence_group", "conflict_group_id", "status", "id"),
        Index("idx_memory_conflict_evidence_element", "element_id"),
        Index("idx_memory_conflict_evidence_source", "source_revision_id"),
        Index("idx_memory_conflict_evidence_draft", "draft_version_id"),
        Index("idx_memory_conflict_evidence_answer", "answer_id"),
        {"comment": "冲突组内的原始说法、规范值及来源证据"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    conflict_group_id = Column(
        BigInteger,
        ForeignKey("t_memory_conflict_group.id", ondelete="RESTRICT"),
        nullable=False,
        comment="所属冲突组 ID",
    )
    element_id = Column(
        BigInteger,
        ForeignKey("t_memory_element.id", ondelete="RESTRICT"),
        comment="提供该说法的记忆元素 ID",
    )
    source_revision_id = Column(
        BigInteger,
        ForeignKey("t_memory_source_revision.id", ondelete="RESTRICT"),
        comment="对应原文或 ASR 版本 ID",
    )
    draft_version_id = Column(
        BigInteger,
        ForeignKey("t_memory_draft_version.id", ondelete="RESTRICT"),
        comment="对应整理稿版本 ID",
    )
    answer_id = Column(
        BigInteger,
        ForeignKey("t_memory_review_answer.id", ondelete="RESTRICT"),
        comment="提供该说法的同组补述回答 ID",
    )
    normalized_value = Column(JSONB, nullable=False, comment="时间、地点或事件线索的规范值")
    evidence_payload = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="整理稿、原文及原声的位置，或补述中的引用区间",
    )
    evidence_key = Column(String(64), nullable=False, comment="来源、叙述者及事实含义的 SHA-256")
    status = Column(
        String(24),
        nullable=False,
        server_default=text("'active'"),
        comment="证据有效或已因来源修订、撤回而被替代",
    )
