"""[新增] 保存会话成员及成员的连续已读位置。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, text

from app.models.base import AuditFields, Base


class ChatConversationMember(AuditFields, Base):
    __tablename__ = "t_chat_conversation_member"
    __table_args__ = (
        CheckConstraint("last_read_seq >= 0", name="ck_chat_member_last_read_seq"),
        Index("idx_chat_member_user_conversation", "user_id", "conversation_id"),
        {"comment": "站内会话成员及个人已读进度"},
    )

    conversation_id = Column(
        BigInteger,
        ForeignKey("t_chat_conversation.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="所属会话 ID",
    )
    user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="会话成员账号 ID",
    )
    last_read_seq = Column(
        BigInteger, nullable=False, server_default=text("0"), comment="本人连续已读的最后消息序号"
    )
