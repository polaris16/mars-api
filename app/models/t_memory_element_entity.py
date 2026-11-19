"""ORM model for links between memory elements and life entities."""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Numeric, PrimaryKeyConstraint, String, Text, text

from app.models.base import Base


class MemoryElementEntity(Base):
    __tablename__ = "t_memory_element_entity"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_memory_element_entity_pkey"),
        CheckConstraint("relation_type IN ('subject', 'participant', 'location', 'mentioned', 'other')", name="ck_t_memory_element_entity_relation_type"),
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="ck_t_memory_element_entity_confidence"),
        Index("uq_t_memory_element_entity_active", "memory_element_id", "entity_id", "relation_type", unique=True, postgresql_where=text("is_del = false")),
        Index("idx_t_memory_element_entity_entity", "entity_id", "memory_element_id"),
        {"comment": "Links between memory elements and life entities"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="Id")
    memory_element_id = Column(BigInteger, ForeignKey("t_memory_element.id", ondelete="CASCADE", name="fk_t_memory_element_entity_element"), nullable=False, comment="Memory element id")
    entity_id = Column(BigInteger, ForeignKey("t_life_entity.id", ondelete="RESTRICT", name="fk_t_memory_element_entity_entity"), nullable=False, comment="Entity id")
    relation_type = Column(String(24), nullable=False, server_default=text("'mentioned'"), comment="Relation type")
    confidence = Column(Numeric(5, 4), nullable=True, comment="Confidence")
    evidence_text = Column(Text, nullable=True, comment="Evidence text")
    created_by = Column(BigInteger, nullable=True, comment="Created by")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="Created at")
    updated_by = Column(BigInteger, nullable=True, comment="Updated by")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="Updated at")
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="Is del")
