"""ORM model for people, places, and organizations in a user life graph."""

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


class LifeEntity(Base):
    __tablename__ = "t_life_entity"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_life_entity_pkey"),
        CheckConstraint(
            "entity_type IN ('person', 'place', 'organization')", name="ck_t_life_entity_type"
        ),
        CheckConstraint(
            "jsonb_typeof(attributes) = 'object'", name="ck_t_life_entity_attributes_object"
        ),
        Index("idx_t_life_entity_owner_type", "owner_user_id", "entity_type", "id"),
        {"comment": "人生图谱中的人物、地点和机构"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    owner_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_life_entity_owner"),
        nullable=False,
        comment="人生图谱所有者的用户 ID",
    )
    entity_type = Column(
        String(24), nullable=False, comment="实体类型：person 人物、place 地点、organization 机构"
    )
    canonical_name = Column(String(200), nullable=False, comment="实体归一化后的标准名称")
    relationship_to_author = Column(
        String(100), nullable=True, comment="实体与回忆录主人之间的关系说明"
    )
    summary_profile = Column(Text, nullable=True, comment="用于回忆录整理的实体简介")
    attributes = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="按实体类型补充的结构化属性，JSON 对象",
    )
    first_mentioned_fragment_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_fragment.id", ondelete="SET NULL", name="fk_t_life_entity_first_fragment"
        ),
        nullable=True,
        comment="最早提及该实体的来源片段 ID",
    )
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
