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

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment='Primary key identifier')  # [UPDATED] Added column comment.
    session_id = Column(BigInteger, ForeignKey("t_memory_session.id", ondelete="CASCADE", name="fk_t_memory_session_member_session"), nullable=False, comment='Memory session ID')  # [UPDATED] Added column comment.
    user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_memory_session_member_user"), nullable=False, comment='Related user account ID')  # [UPDATED] Added column comment.
    role = Column(String(24), nullable=False, comment='Member role and access level in the session')  # [UPDATED] Added column comment.
    source_relationship_id = Column(BigInteger, ForeignKey("t_user_relationship.id", ondelete="SET NULL", name="fk_t_memory_session_member_relationship"), comment='Relationship supporting the session access grant')  # [UPDATED] Added column comment.
    can_manage_members = Column(Boolean, nullable=False, server_default=text("false"), comment='Whether this member may manage other session members')  # [UPDATED] Added column comment.
    status = Column(String(20), nullable=False, server_default=text("'active'"), comment='Lifecycle status')  # [UPDATED] Added column comment.
    granted_by_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="RESTRICT", name="fk_t_memory_session_member_granted_by"), nullable=False, comment='User who granted this session membership')  # [UPDATED] Added column comment.
    granted_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='Timestamp when session access was granted')  # [UPDATED] Added column comment.
    valid_until = Column(DateTime(timezone=True), comment='End of the validity period')  # [UPDATED] Added column comment.
    revoked_at = Column(DateTime(timezone=True), comment='Timestamp when the relationship was revoked')  # [UPDATED] Added column comment.
    revocation_reason = Column(String(255), comment='Reason the relationship was revoked')  # [UPDATED] Added column comment.
    created_by = Column(BigInteger, comment='User ID that created this row')  # [UPDATED] Added column comment.
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='Timestamp when this row was created')  # [UPDATED] Added column comment.
    updated_by = Column(BigInteger, comment='User ID that last updated this row')  # [UPDATED] Added column comment.
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='Timestamp when this row was last updated')  # [UPDATED] Added column comment.
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment='Soft-delete flag')  # [UPDATED] Added column comment.
