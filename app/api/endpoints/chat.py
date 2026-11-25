"""[新增] App 站内好友会话接口。"""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.endpoints.benefits import BenefitRoute, Context
from app.schemas.benefits import Id
from app.schemas.chat import (
    ChatMessageView,
    ConversationOpened,
    ConversationPage,
    FriendConversationInput,
    MessagePage,
    ReadPositionInput,
    ReadPositionView,
    SendMessageInput,
)
from app.services import chat_service

# 复用不回显请求内容的错误转换和 no-store 响应，不依赖权益功能开关。
router = APIRouter(prefix="/users/me/conversations", tags=["站内消息"], route_class=BenefitRoute)
MessageCursor = Annotated[int | None, Query(ge=0, le=2**63 - 1)]
PageLimit = Annotated[int, Query(ge=1, le=50)]


@router.get("", response_model=ConversationPage, summary="查询我的站内会话")
def list_my_conversations(
    ctx: Context,
    cursor: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    limit: PageLimit = 20,
):
    return chat_service.list_conversations(ctx, cursor, limit)


@router.post(
    "/friends",
    response_model=ConversationOpened,
    status_code=201,
    responses={200: {"model": ConversationOpened}},
    summary="建立或打开好友会话",
)
def open_friend_conversation(ctx: Context, body: FriendConversationInput, response: Response):
    data, created = chat_service.open_friend_conversation(ctx, body.contact_user_id)
    if not created:
        response.status_code = 200
    return data


@router.get(
    "/{conversation_id}/messages",
    response_model=MessagePage,
    summary="查询历史或增量消息",
)
def list_conversation_messages(
    ctx: Context,
    conversation_id: Id,
    before_seq: MessageCursor = None,
    after_seq: MessageCursor = None,
    limit: PageLimit = 30,
):
    return chat_service.list_messages(ctx, conversation_id, before_seq, after_seq, limit)


@router.post(
    "/{conversation_id}/messages",
    response_model=ChatMessageView,
    status_code=201,
    responses={200: {"model": ChatMessageView}},
    summary="发送好友消息",
)
def send_conversation_message(
    ctx: Context,
    conversation_id: Id,
    body: SendMessageInput,
    response: Response,
):
    data, created = chat_service.send_message(ctx, conversation_id, body)
    if not created:
        response.status_code = 200
    return data


@router.put(
    "/{conversation_id}/read",
    response_model=ReadPositionView,
    summary="更新本人的已读进度",
)
def mark_conversation_read(ctx: Context, conversation_id: Id, body: ReadPositionInput):
    return chat_service.mark_read(ctx, conversation_id, body.read_through_seq)
