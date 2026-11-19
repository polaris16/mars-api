"""认证会话模型。"""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, String, text

from app.models.base import Base


class AuthSession(Base):
    """持久化登录会话；token_hash 是唯一索引，原始 Token 不入库。"""

    __tablename__ = "t_auth_session"
    __table_args__ = (Index("ix_t_auth_session_user_id", "user_id"),)

    id = Column(BigInteger, primary_key=True, comment='Primary key identifier')  # [UPDATED] Added column comment.
    # 用户删除时级联清理其登录会话。
    user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="CASCADE"), nullable=False, comment='Related user account ID')  # [UPDATED] Added column comment.
    # SHA-256 十六进制摘要，唯一约束用于快速定位会话。
    token_hash = Column(String(64), nullable=False, unique=True, comment='SHA-256 hash of the bearer token; raw tokens are never stored')  # [UPDATED] Added column comment.
    # 登录签发时计算的 UTC 到期时间。
    expires_at = Column(DateTime(timezone=True), nullable=False, comment='Authentication token expiration timestamp')  # [UPDATED] Added column comment.
    created_by = Column(BigInteger, nullable=True, comment='User ID that created this row')  # [UPDATED] Added column comment.
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='Timestamp when this row was created')  # [UPDATED] Added column comment.
    updated_by = Column(BigInteger, nullable=True, comment='User ID that last updated this row')  # [UPDATED] Added column comment.
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment='Timestamp when this row was last updated')  # [UPDATED] Added column comment.
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment='Soft-delete flag')  # [UPDATED] Added column comment.
