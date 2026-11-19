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

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(BigInteger, ForeignKey("t_memory_session.id", ondelete="CASCADE", name="fk_t_memory_contributor_session"), nullable=False)
    contributor_type = Column(String(24), nullable=False)
    account_user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="RESTRICT", name="fk_t_memory_contributor_user"))
    session_member_id = Column(BigInteger)
    relationship_id = Column(BigInteger, ForeignKey("t_user_relationship.id", ondelete="SET NULL", name="fk_t_memory_contributor_relationship"))
    display_name = Column(String(100), nullable=False)
    relationship_label = Column(String(100))
    identity_status = Column(String(20), nullable=False, server_default=text("'unverified'"))
    consent_status = Column(String(20), nullable=False, server_default=text("'pending'"))
    consented_at = Column(DateTime(timezone=True))
    created_by = Column(BigInteger)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_by = Column(BigInteger)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    is_del = Column(Boolean, nullable=False, server_default=text("false"))
