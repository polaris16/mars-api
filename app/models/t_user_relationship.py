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

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    principal_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_user_relationship_principal"), nullable=False)
    delegate_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_user_relationship_delegate"), nullable=False)
    relationship_type = Column(String(30), nullable=False)
    relationship_label = Column(String(100))
    status = Column(String(20), nullable=False, server_default=text("'pending'"))
    authorization_method = Column(String(30), nullable=False)
    authorization_reference = Column(String(255))
    verified_by_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="SET NULL", name="fk_t_user_relationship_verifier"))
    valid_from = Column(DateTime(timezone=True))
    valid_until = Column(DateTime(timezone=True))
    accepted_at = Column(DateTime(timezone=True))
    revoked_at = Column(DateTime(timezone=True))
    revocation_reason = Column(String(255))
    created_by = Column(BigInteger)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_by = Column(BigInteger)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    is_del = Column(Boolean, nullable=False, server_default=text("false"))
