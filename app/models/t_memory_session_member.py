"""Registered users granted access to a memory session."""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, PrimaryKeyConstraint, String, UniqueConstraint, text

from app.models.base import Base


class MemorySessionMember(Base):
    __tablename__ = "t_memory_session_member"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_memory_session_member_pkey"),
        UniqueConstraint("session_id", "user_id", name="uq_t_memory_session_member_session_user"),
        UniqueConstraint("session_id", "id", name="uq_t_memory_session_member_session_id"),
        CheckConstraint("role IN ('viewer', 'contributor', 'editor', 'delegate')", name="ck_t_memory_session_member_role"),
        CheckConstraint("status IN ('active', 'revoked', 'expired')", name="ck_t_memory_session_member_status"),
        Index("idx_t_memory_session_member_user_status", "user_id", "status"),
        {"comment": "记忆会话注册用户访问授权"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(BigInteger, ForeignKey("t_memory_session.id", ondelete="CASCADE", name="fk_t_memory_session_member_session"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_memory_session_member_user"), nullable=False)
    role = Column(String(24), nullable=False)
    source_relationship_id = Column(BigInteger, ForeignKey("t_user_relationship.id", ondelete="SET NULL", name="fk_t_memory_session_member_relationship"))
    can_manage_members = Column(Boolean, nullable=False, server_default=text("false"))
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    granted_by_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="RESTRICT", name="fk_t_memory_session_member_granted_by"), nullable=False)
    granted_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    valid_until = Column(DateTime(timezone=True))
    revoked_at = Column(DateTime(timezone=True))
    revocation_reason = Column(String(255))
    created_by = Column(BigInteger)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_by = Column(BigInteger)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    is_del = Column(Boolean, nullable=False, server_default=text("false"))
