"""认证会话模型。"""

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, String, text

from app.models.base import Base


class AuthSession(Base):
    """持久化登录会话；token_hash 是唯一索引，原始 Token 不入库。"""

    __tablename__ = "auth_session"
    __table_args__ = (Index("ix_auth_session_user_id", "user_id"),)

    id = Column(BigInteger, primary_key=True)
    # 用户删除时级联清理其登录会话。
    user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="CASCADE"), nullable=False)
    # SHA-256 十六进制摘要，唯一约束用于快速定位会话。
    token_hash = Column(String(64), nullable=False, unique=True)
    # 登录签发时计算的 UTC 到期时间。
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
