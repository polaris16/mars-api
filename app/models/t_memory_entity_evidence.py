"""[新增] 实体关联及别名的逐项来源证据。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import AuditFields, Base


class MemoryEntityEvidence(AuditFields, Base):
    __tablename__ = "t_memory_entity_evidence"
    __table_args__ = (
        UniqueConstraint("element_entity_id", "evidence_key", name="uq_memory_entity_evidence"),
        Index("idx_memory_entity_evidence_alias", "alias_id"),
        CheckConstraint("jsonb_typeof(evidence) = 'object'", name="ck_memory_entity_evidence"),
        {"comment": "实体关联及别名的逐项来源证据"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    element_entity_id = Column(
        BigInteger, ForeignKey("t_memory_element_entity.id", ondelete="CASCADE"),
        nullable=False, comment="对应的记忆元素实体关联 ID",
    )
    alias_id = Column(
        BigInteger, ForeignKey("t_life_entity_alias.id", ondelete="SET NULL"),
        comment="同一条证据支持的别名 ID",
    )
    evidence = Column(JSONB, nullable=False, comment="整理稿段落及原文位置")
    evidence_key = Column(String(64), nullable=False, comment="证据和称谓的去重指纹")
