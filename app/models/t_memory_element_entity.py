"""ORM model for links between memory elements and life entities."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)

from app.models.base import Base


class MemoryElementEntity(Base):
    __tablename__ = "t_memory_element_entity"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_memory_element_entity_pkey"),
        CheckConstraint(
            "relation_type IN ('subject', 'participant', 'location', 'mentioned', 'other')",
            name="ck_t_memory_element_entity_relation_type",
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="ck_t_memory_element_entity_confidence",
        ),
        Index(
            "uq_t_memory_element_entity_active",
            "memory_element_id",
            "entity_id",
            "relation_type",
            unique=True,
            postgresql_where=text("is_del = false"),
        ),
        Index("idx_t_memory_element_entity_entity", "entity_id", "memory_element_id"),
        {"comment": "记忆元素与人生图谱实体的关联"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    memory_element_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_element.id", ondelete="CASCADE", name="fk_t_memory_element_entity_element"
        ),
        nullable=False,
        comment="关联的记忆元素 ID",
    )
    entity_id = Column(
        BigInteger,
        ForeignKey(
            "t_life_entity.id", ondelete="RESTRICT", name="fk_t_memory_element_entity_entity"
        ),
        nullable=False,
        comment="关联的人生实体 ID",
    )
    relation_type = Column(
        String(24),
        nullable=False,
        server_default=text("'mentioned'"),
        comment="实体角色：subject 主体、participant 参与者、location 地点、mentioned 提及、other 其他",
    )
    confidence = Column(Numeric(5, 4), nullable=True, comment="元素与实体关联的置信度，取值 0 至 1")
    evidence_text = Column(Text, nullable=True, comment="支持此关联的原文证据")
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
