"""[新增] 保存按会话排序的聊天文字及青年伙伴卡片。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)

from app.models.base import AuditFields, Base


class ChatMessage(AuditFields, Base):
    __tablename__ = "t_chat_message"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "sender_user_id"],
            ["t_chat_conversation_member.conversation_id", "t_chat_conversation_member.user_id"],
            name="fk_chat_message_sender_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("conversation_id", "sequence_no", name="uq_chat_message_sequence"),
        UniqueConstraint(
            "conversation_id", "sender_user_id", "client_message_id", name="uq_chat_message_request"
        ),
        CheckConstraint("sequence_no > 0", name="ck_chat_message_sequence"),
        CheckConstraint(
            "(message_type = 'text' AND body_text IS NOT NULL AND "
            "char_length(body_text) BETWEEN 1 AND 2000 AND btrim(body_text) <> '' "
            "AND partner_id IS NULL) OR "
            "(message_type = 'partner_card' AND body_text IS NULL AND partner_id IS NOT NULL)",
            name="ck_chat_message_content",
        ),
        Index(
            "idx_chat_message_sender_sequence", "conversation_id", "sender_user_id", "sequence_no"
        ),
        {"comment": "站内聊天历史消息"},
    )

    id = Column(BigInteger, primary_key=True, comment="消息 ID")
    conversation_id = Column(BigInteger, nullable=False, comment="所属会话 ID")
    sequence_no = Column(BigInteger, nullable=False, comment="会话内递增的消息序号")
    sender_user_id = Column(BigInteger, nullable=False, comment="发送者账号 ID")
    client_message_id = Column(Uuid, nullable=False, comment="客户端发送及重试标识")
    message_type = Column(
        String(30), nullable=False, comment="内容类型：text 文字、partner_card 青年伙伴卡片"
    )
    body_text = Column(Text, comment="文字消息正文")
    partner_id = Column(
        BigInteger,
        ForeignKey("t_youth_partner_profile.id", ondelete="RESTRICT"),
        comment="分享卡片所引用的青年伙伴资料 ID",
    )
