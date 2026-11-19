"""Registered-user family, care, and delegation relationships."""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, PrimaryKeyConstraint, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class UserRelationship(Base):
    __tablename__ = "t_user_relationship"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_user_relationship_pkey"),
        CheckConstraint("subject_user_id <> related_user_id", name="ck_t_user_relationship_distinct_users"),
        CheckConstraint("relationship_type IN ('family', 'agent', 'caregiver', 'other')", name="ck_t_user_relationship_type"),
        CheckConstraint("relation_code IN ('parent_of', 'child_of', 'spouse_of', 'sibling_of', 'related_to', 'agent_for', 'caregiver_for', 'other')", name="ck_t_user_relationship_relation_code"),
        CheckConstraint("status IN ('pending', 'active', 'rejected', 'revoked', 'expired')", name="ck_t_user_relationship_status"),
        CheckConstraint("authorization_method IN ('in_app', 'offline_agreement', 'admin_verified')", name="ck_t_user_relationship_auth_method"),
        UniqueConstraint("relationship_pair_id", "subject_user_id", name="uq_t_user_relationship_pair_subject"),
        Index("idx_t_user_relationship_subject_status", "subject_user_id", "status"),
        Index("idx_t_user_relationship_related_status", "related_user_id", "status"),
        {"comment": "注册用户间的亲友或代理关系"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment='Primary key identifier')  # [UPDATED] Added column comment.
    relationship_pair_id = Column(UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"), comment="Shared identifier for reciprocal relationship rows")
    subject_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_user_relationship_subject"), nullable=False, comment="User represented as the subject of this directional relation")
    related_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_user_relationship_related"), nullable=False, comment="Other user referenced by this directional relation")
    relationship_type = Column(String(30), nullable=False, comment='Relationship category: family, agent, caregiver, or other')  # [UPDATED] Added column comment.
    relation_code = Column(String(30), nullable=False, comment="Directional relation code; reciprocal family codes are stored in paired rows")
    relationship_label = Column(String(100), comment='Free-form relationship label')  # [UPDATED] Added column comment.
    status = Column(String(20), nullable=False, server_default=text("'pending'"), comment='Lifecycle status')  # [UPDATED] Added column comment.
    authorization_method = Column(String(30), nullable=False, comment='Method used to establish or verify authorization')  # [UPDATED] Added column comment.
    authorization_reference = Column(String(255), comment='Reference to supporting authorization evidence')  # [UPDATED] Added column comment.
    initiated_by_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="RESTRICT", name="fk_t_user_relationship_initiator"), nullable=False, comment="User who initiated the relationship invitation")
    verified_by_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="SET NULL", name="fk_t_user_relationship_verifier"), comment='User who verified the relationship')  # [UPDATED] Added column comment.
    valid_from = Column(DateTime(timezone=True), comment='Beginning of the validity period')  # [UPDATED] Added column comment.
    valid_until = Column(DateTime(timezone=True), comment='End of the validity period')  # [UPDATED] Added column comment.
    accepted_at = Column(DateTime(timezone=True), comment='Timestamp when the relationship was accepted')  # [UPDATED] Added column comment.
    revoked_at = Column(DateTime(timezone=True), comment='Timestamp when the relationship was revoked')  # [UPDATED] Added column comment.
    revocation_reason = Column(String(255), comment='Reason the relationship was revoked')  # [UPDATED] Added column comment.
    created_by = Column(BigInteger, comment='User ID that created this row')  # [UPDATED] Added column comment.
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='Timestamp when this row was created')  # [UPDATED] Added column comment.
    updated_by = Column(BigInteger, comment='User ID that last updated this row')  # [UPDATED] Added column comment.
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='Timestamp when this row was last updated')  # [UPDATED] Added column comment.
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment='Soft-delete flag')  # [UPDATED] Added column comment.
