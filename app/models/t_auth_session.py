"""认证会话模型。"""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, String, text

from app.models.base import Base


class AuthSession(Base):
    """持久化登录会话；token_hash 是唯一索引，原始 Token 不入库。"""

    __tablename__ = "t_auth_session"
    __table_args__ = (
        Index("ix_t_auth_session_user_id", "user_id"),
        {"comment": "用户登录认证会话"},
    )

    id = Column(BigInteger, primary_key=True, comment="主键 ID")  # [UPDATED] Added column comment.
    # 用户删除时级联清理其登录会话。
    user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="CASCADE"),
        nullable=False,
        comment="持有登录会话的用户 ID",
    )
    # SHA-256 十六进制摘要，唯一约束用于快速定位会话。
    token_hash = Column(
        String(64), nullable=False, unique=True, comment="Bearer Token 的 SHA-256 摘要，原文不入库"
    )
    # 登录签发时计算的 UTC 到期时间。
    expires_at = Column(
        DateTime(timezone=True), nullable=False, comment="登录会话到期时间"
    )
    created_by = Column(
        BigInteger, nullable=True, comment="创建人 ID"
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )
    updated_by = Column(
        BigInteger, nullable=True, comment="更新人 ID"
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )
    is_del = Column(
        Boolean, nullable=False, server_default=text("false"), comment="是否软删除"
    )
