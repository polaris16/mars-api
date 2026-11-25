"""[新增] 站内会话、消息翻页与已读契约。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.benefits import BenefitInput, Id


Seq = Annotated[int, Field(ge=0, le=2**63 - 1)]
TextBody = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000, pattern=r"^[^\x00]+$"),
]


class FriendConversationInput(BenefitInput):
    contact_user_id: Id


class ConversationOpened(BaseModel):
    conversation_id: int


class TextMessageInput(BenefitInput):
    type: Literal["text"]
    client_message_id: UUID
    text: TextBody


class PartnerCardInput(BenefitInput):
    type: Literal["partner_card"]
    client_message_id: UUID
    partner_id: Id


SendMessageInput = Annotated[TextMessageInput | PartnerCardInput, Field(discriminator="type")]


class PartnerCardView(BaseModel):
    is_available: bool
    partner_id: int | None = None
    display_name: str | None = None


class ChatMessageView(BaseModel):
    id: int
    sequence_no: int
    type: Literal["text", "partner_card"]
    sender_user_id: int
    client_message_id: UUID
    created_at: datetime
    text: str | None = None
    partner_card: PartnerCardView | None = None


class MessagePage(BaseModel):
    items: list[ChatMessageView]
    has_more: bool
    can_send: bool


class ConversationPeer(BaseModel):
    id: int
    display_name: str | None = None
    avatar_url: str | None = None


class ConversationView(BaseModel):
    conversation_id: int
    kind: Literal["friend"]
    peer: ConversationPeer
    last_message: ChatMessageView | None
    last_message_at: datetime
    last_read_seq: int
    unread_count: int
    can_send: bool


class ConversationPage(BaseModel):
    items: list[ConversationView]
    next_cursor: str | None


class ReadPositionInput(BenefitInput):
    read_through_seq: Seq


class ReadPositionView(BaseModel):
    last_read_seq: int
    unread_count: int
