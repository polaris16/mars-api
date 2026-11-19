"""Registered-user family, care, and delegation relationships."""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, PrimaryKeyConstraint, String, UniqueConstraint, text

from app.models.base import Base


class UserRelationship(Base):
    __tablename__ = "t_user_relationship"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_user_relationship_pkey"),
        CheckConstraint("principal_user_id <> delegate_user_id", name="ck_t_user_relationship_distinct_users"),
        CheckConstraint("relationship_type IN ('family', 'agent', 'caregiver', 'other')", name="ck_t_user_relationship_type"),
        CheckConstraint("status IN ('pending', 'active', 'rejected', 'revoked', 'expired')", name="ck_t_user_relationship_status"),
        CheckConstraint("authorization_method IN ('in_app', 'offline_agreement', 'admin_verified')", name="ck_t_user_relationship_auth_method"),
        UniqueConstraint("principal_user_id", "delegate_user_id", "relationship_type", name="uq_t_user_relationship_pair_type"),
        Index("idx_t_user_relationship_delegate_status", "delegate_user_id", "status"),
        {"comment": "注册用户间的亲友或代理关系"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment='Primary key identifier')  # [UPDATED] Added column comment.
    principal_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_user_relationship_principal"), nullable=False, comment='User who owns or grants the relationship')  # [UPDATED] Added column comment.
    delegate_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_user_relationship_delegate"), nullable=False, comment='Related family member, caregiver, or delegate user')  # [UPDATED] Added column comment.
    relationship_type = Column(String(30), nullable=False, comment='Relationship category: family, agent, caregiver, or other')  # [UPDATED] Added column comment.
    relationship_label = Column(String(100), comment='Free-form relationship label')  # [UPDATED] Added column comment.
    status = Column(String(20), nullable=False, server_default=text("'pending'"), comment='Lifecycle status')  # [UPDATED] Added column comment.
    authorization_method = Column(String(30), nullable=False, comment='Method used to establish or verify authorization')  # [UPDATED] Added column comment.
    authorization_reference = Column(String(255), comment='Reference to supporting authorization evidence')  # [UPDATED] Added column comment.
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
