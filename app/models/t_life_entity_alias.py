"""ORM model for aliases of normalized life entities."""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Numeric, PrimaryKeyConstraint, String, text

from app.models.base import Base


class LifeEntityAlias(Base):
    __tablename__ = "t_life_entity_alias"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_life_entity_alias_pkey"),
        CheckConstraint("length(btrim(alias_name)) > 0", name="ck_t_life_entity_alias_not_blank"),
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="ck_t_life_entity_alias_confidence"),
        Index("uq_t_life_entity_alias_active", "entity_id", "alias_name", unique=True, postgresql_where=text("is_del = false")),
        Index("idx_t_life_entity_alias_name", "alias_name"),
        {"comment": "Aliases of normalized life entities"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="Id")
    entity_id = Column(BigInteger, ForeignKey("t_life_entity.id", ondelete="CASCADE", name="fk_t_life_entity_alias_entity"), nullable=False, comment="Entity id")
    alias_name = Column(String(200), nullable=False, comment="Alias name")
    alias_type = Column(String(30), nullable=False, server_default=text("'other'"), comment="Alias type")
    source_fragment_id = Column(BigInteger, ForeignKey("t_memory_fragment.id", ondelete="SET NULL", name="fk_t_life_entity_alias_fragment"), nullable=True, comment="Source fragment id")
    confidence = Column(Numeric(5, 4), nullable=True, comment="Confidence")
    created_by = Column(BigInteger, nullable=True, comment="Created by")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="Created at")
    updated_by = Column(BigInteger, nullable=True, comment="Updated by")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="Updated at")
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="Is del")
