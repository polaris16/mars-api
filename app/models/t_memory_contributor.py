"""Material providers within a memory session, including guests."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)

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
        CheckConstraint(
            "contributor_type IN ('owner', 'registered_user', 'guest')",
            name="ck_t_memory_contributor_type",
        ),
        CheckConstraint(
            "identity_status IN ('unverified', 'verified')",
            name="ck_t_memory_contributor_identity_status",
        ),
        CheckConstraint(
            "consent_status IN ('pending', 'accepted', 'withdrawn')",
            name="ck_t_memory_contributor_consent_status",
        ),
        CheckConstraint(
            "(contributor_type = 'guest' AND account_user_id IS NULL) OR "
            "(contributor_type IN ('owner', 'registered_user') AND account_user_id IS NOT NULL)",
            name="ck_t_memory_contributor_account_type",
        ),
        {"comment": "记忆会话中的素材提供者身份"},
    )

    id = Column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键 ID"
    )
    session_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_session.id", ondelete="CASCADE", name="fk_t_memory_contributor_session"
        ),
        nullable=False,
        comment="所属记忆会话 ID",
    )
    contributor_type = Column(
        String(24),
        nullable=False,
        comment="提供者类型：owner 会话主人、registered_user 注册用户、guest 访客",
    )
    account_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT", name="fk_t_memory_contributor_user"),
        comment="素材提供者对应的注册用户 ID，访客为空",
    )
    session_member_id = Column(
        BigInteger, comment="赋予贡献资格的会话成员授权 ID"
    )
    relationship_id = Column(
        BigInteger,
        ForeignKey(
            "t_user_relationship.id",
            ondelete="SET NULL",
            name="fk_t_memory_contributor_relationship",
        ),
        comment="提供者资格关联的亲属关系 ID",
    )
    display_name = Column(
        String(100), nullable=False, comment="素材提供者的会话内显示名"
    )
    relationship_label = Column(
        String(100), comment="提供者的自定义关系称谓"
    )
    identity_status = Column(
        String(20),
        nullable=False,
        server_default=text("'unverified'"),
        comment="身份核验状态：unverified 未核验、verified 已核验",
    )
    consent_status = Column(
        String(20),
        nullable=False,
        server_default=text("'pending'"),
        comment="提供者同意状态：pending 待同意、accepted 已同意、withdrawn 已撤回",
    )
    consented_at = Column(
        DateTime(timezone=True), comment="最近一次同意提供素材的时间"
    )
    created_by = Column(BigInteger, comment="创建人 ID")  # [UPDATED] Added column comment.
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )
    updated_by = Column(BigInteger, comment="更新人 ID")  # [UPDATED] Added column comment.
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )
    is_del = Column(
        Boolean, nullable=False, server_default=text("false"), comment="是否软删除"
    )
