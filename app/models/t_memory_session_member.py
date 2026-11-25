"""Registered users granted access to a memory session."""

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
    UniqueConstraint,
    text,
)

from app.models.base import Base


class MemorySessionMember(Base):
    __tablename__ = "t_memory_session_member"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_memory_session_member_pkey"),
        UniqueConstraint("session_id", "user_id", name="uq_t_memory_session_member_session_user"),
        UniqueConstraint("session_id", "id", name="uq_t_memory_session_member_session_id"),
        CheckConstraint(
            "role IN ('viewer', 'contributor', 'editor', 'delegate')",
            name="ck_t_memory_session_member_role",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked', 'expired')", name="ck_t_memory_session_member_status"
        ),
        Index("idx_t_memory_session_member_user_status", "user_id", "status"),
        {"comment": "记忆会话注册用户访问授权"},
    )

    id = Column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键 ID"
    )
    session_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_session.id", ondelete="CASCADE", name="fk_t_memory_session_member_session"
        ),
        nullable=False,
        comment="所属记忆会话 ID",
    )
    user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_memory_session_member_user"),
        nullable=False,
        comment="获得会话授权的用户 ID",
    )
    role = Column(
        String(24),
        nullable=False,
        comment="会话角色：viewer 查看者、contributor 贡献者、editor 编辑者、delegate 代理",
    )
    source_relationship_id = Column(
        BigInteger,
        ForeignKey(
            "t_user_relationship.id",
            ondelete="SET NULL",
            name="fk_t_memory_session_member_relationship",
        ),
        comment="会话授权所依据的用户关系 ID",
    )
    can_manage_members = Column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="是否允许代理管理普通会话成员",
    )
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'active'"),
        comment="授权状态：active 有效、revoked 已撤销、expired 已过期",
    )
    granted_by_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT", name="fk_t_memory_session_member_granted_by"),
        nullable=False,
        comment="授予会话权限的用户 ID",
    )
    granted_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="最近一次授予会话权限的时间",
    )
    valid_until = Column(
        DateTime(timezone=True), comment="会话授权截止时间，空值表示无单独期限"
    )
    revoked_at = Column(
        DateTime(timezone=True), comment="撤销会话授权的时间"
    )
    revocation_reason = Column(
        String(255), comment="撤销会话授权的原因"
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
