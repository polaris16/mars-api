"""[新增] 保存好友会话及最后提交的消息位置。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index, String, Uuid, text

from app.models.base import AuditFields, Base


class ChatConversation(AuditFields, Base):
    __tablename__ = "t_chat_conversation"
    __table_args__ = (
        CheckConstraint("kind = 'friend'", name="ck_chat_conversation_kind"),
        CheckConstraint(
            "last_message_seq >= 0 AND (last_message_seq = 0) = (last_message_at IS NULL)",
            name="ck_chat_conversation_last_message",
        ),
        Index(
            "idx_chat_conversation_inbox",
            "last_message_at",
            "id",
            postgresql_where=text("is_del = false AND last_message_at IS NOT NULL"),
        ),
        {"comment": "站内一对一会话及最近消息位置"},
    )

    id = Column(BigInteger, primary_key=True, comment="会话 ID")
    kind = Column(String(30), nullable=False, comment="会话类型：friend 好友聊天")
    relationship_pair_id = Column(Uuid, nullable=False, unique=True, comment="原双向好友关系组 ID")
    last_message_seq = Column(
        BigInteger, nullable=False, server_default=text("0"), comment="最后已提交的会话内消息序号"
    )
    last_message_at = Column(DateTime(timezone=True), comment="最后一条消息的服务端时间")
