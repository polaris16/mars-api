"""ORM model for people, places, and organizations in a user life graph."""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, PrimaryKeyConstraint, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base


class LifeEntity(Base):
    __tablename__ = "t_life_entity"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_life_entity_pkey"),
        CheckConstraint("entity_type IN ('person', 'place', 'organization')", name="ck_t_life_entity_type"),
        CheckConstraint("jsonb_typeof(attributes) = 'object'", name="ck_t_life_entity_attributes_object"),
        Index("idx_t_life_entity_owner_type", "owner_user_id", "entity_type", "id"),
        {"comment": "People, places, and organizations in a user life graph"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="Id")
    owner_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_life_entity_owner"), nullable=False, comment="Owner user id")
    entity_type = Column(String(24), nullable=False, comment="Entity type")
    canonical_name = Column(String(200), nullable=False, comment="Canonical name")
    relationship_to_author = Column(String(100), nullable=True, comment="Relationship to author")
    summary_profile = Column(Text, nullable=True, comment="Summary profile")
    attributes = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), comment="Attributes")
    first_mentioned_fragment_id = Column(BigInteger, ForeignKey("t_memory_fragment.id", ondelete="SET NULL", name="fk_t_life_entity_first_fragment"), nullable=True, comment="First mentioned fragment id")
    created_by = Column(BigInteger, nullable=True, comment="Created by")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="Created at")
    updated_by = Column(BigInteger, nullable=True, comment="Updated by")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="Updated at")
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="Is del")
