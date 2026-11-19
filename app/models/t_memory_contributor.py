"""Material providers within a memory session, including guests."""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint, PrimaryKeyConstraint, String, UniqueConstraint, text

from app.models.base import Base


class MemoryContributor(Base):
    __tablename__ = "t_memory_contributor"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_memory_contributor_pkey"),
        UniqueConstraint("session_id", "id", name="uq_t_memory_contributor_session_id"),
        ForeignKeyConstraint(
            ["session_id", "session_member_id"],
            ["t_memory_session_member.session_id", "t_memory_session_member.id"],
            ondelete="RESTRICT",
            name="fk_t_memory_contributor_session_member",
        ),
        CheckConstraint("contributor_type IN ('owner', 'registered_user', 'guest')", name="ck_t_memory_contributor_type"),
        CheckConstraint("identity_status IN ('unverified', 'verified')", name="ck_t_memory_contributor_identity_status"),
        CheckConstraint("consent_status IN ('pending', 'accepted', 'withdrawn')", name="ck_t_memory_contributor_consent_status"),
        CheckConstraint(
            "(contributor_type = 'guest' AND account_user_id IS NULL) OR "
            "(contributor_type IN ('owner', 'registered_user') AND account_user_id IS NOT NULL)",
            name="ck_t_memory_contributor_account_type",
        ),
        {"comment": "记忆会话中的素材提供者身份"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment='Primary key identifier')  # [UPDATED] Added column comment.
    session_id = Column(BigInteger, ForeignKey("t_memory_session.id", ondelete="CASCADE", name="fk_t_memory_contributor_session"), nullable=False, comment='Memory session ID')  # [UPDATED] Added column comment.
    contributor_type = Column(String(24), nullable=False, comment='Provider category: owner, registered user, or guest')  # [UPDATED] Added column comment.
    account_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="RESTRICT", name="fk_t_memory_contributor_user"), comment='Registered account representing this contributor')  # [UPDATED] Added column comment.
    session_member_id = Column(BigInteger, comment='Session membership authorizing this contributor')  # [UPDATED] Added column comment.
    relationship_id = Column(BigInteger, ForeignKey("t_user_relationship.id", ondelete="SET NULL", name="fk_t_memory_contributor_relationship"), comment='Relationship associated with this contributor')  # [UPDATED] Added column comment.
    display_name = Column(String(100), nullable=False, comment='Name displayed for the material contributor')  # [UPDATED] Added column comment.
    relationship_label = Column(String(100), comment='Free-form relationship label')  # [UPDATED] Added column comment.
    identity_status = Column(String(20), nullable=False, server_default=text("'unverified'"), comment='Whether this contributor identity has been verified')  # [UPDATED] Added column comment.
    consent_status = Column(String(20), nullable=False, server_default=text("'pending'"), comment='Contributor consent lifecycle status')  # [UPDATED] Added column comment.
    consented_at = Column(DateTime(timezone=True), comment='Timestamp when the contributor consented')  # [UPDATED] Added column comment.
    created_by = Column(BigInteger, comment='User ID that created this row')  # [UPDATED] Added column comment.
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='Timestamp when this row was created')  # [UPDATED] Added column comment.
    updated_by = Column(BigInteger, comment='User ID that last updated this row')  # [UPDATED] Added column comment.
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='Timestamp when this row was last updated')  # [UPDATED] Added column comment.
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment='Soft-delete flag')  # [UPDATED] Added column comment.
