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
        {"comment": "人生图谱实体的别名"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    entity_id = Column(BigInteger, ForeignKey("t_life_entity.id", ondelete="CASCADE", name="fk_t_life_entity_alias_entity"), nullable=False, comment="关联的人生实体 ID")
    alias_name = Column(String(200), nullable=False, comment="原素材中的实体别名")
    alias_type = Column(String(30), nullable=False, server_default=text("'other'"), comment="别名类型，other 表示未细分")
    source_fragment_id = Column(BigInteger, ForeignKey("t_memory_fragment.id", ondelete="SET NULL", name="fk_t_life_entity_alias_fragment"), nullable=True, comment="提供此别名的来源片段 ID")
    confidence = Column(Numeric(5, 4), nullable=True, comment="该别名指向目标实体的置信度，取值 0 至 1")
    created_by = Column(BigInteger, nullable=True, comment="创建人 ID")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_by = Column(BigInteger, nullable=True, comment="更新人 ID")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="是否软删除")
