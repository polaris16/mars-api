"""[新增] 好友会话、持久消息、上翻和个人已读位置。"""

from base64 import b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from app.core.config import get_settings
from app.models import (
    ChatConversation,
    ChatConversationMember,
    ChatMessage,
    User,
    UserRelationship,
    YouthPartnerProfile,
)
from app.schemas.chat import TextMessageInput
from app.services.benefit_common import (
    BenefitError,
    conflict,
    flush_unique,
    new_row,
    now_utc,
    stamp,
    utc,
)
from app.services.user_relationship_service import UserRelationshipService
from app.services.youth_partner_schedule import public_partners


def missing():
    return BenefitError(404, "chat_resource_not_found", "会话或相关资源不存在")


def _contact_groups(actor_id, peer_id, now):
    relation = UserRelationship
    return select(relation.relationship_pair_id).where(
        relation.subject_user_id == actor_id,
        relation.related_user_id == peer_id,
        relation.relationship_type == "contact",
        relation.relation_code == "contact_of",
        relation.is_del.is_(False),
        UserRelationshipService._current_contact(now),
    )


def _lock_contact_group(db, pair_id):
    # 好友解除也按关系行 ID 排序上锁。锁后用新查询读取两侧的当前状态。
    relation = UserRelationship
    db.scalars(
        select(relation.id)
        .where(relation.relationship_pair_id == pair_id, relation.is_del.is_(False))
        .order_by(relation.id)
        .with_for_update()
    ).all()


def _can_send(db, actor_id, peer_id, pair_id):
    active_peer = select(User.id).where(User.id == peer_id, User.is_del.is_(False)).exists()
    active_pair = (
        _contact_groups(actor_id, peer_id, now_utc())
        .where(UserRelationship.relationship_pair_id == pair_id)
        .exists()
    )
    return bool(db.scalar(select(active_peer & active_pair)))


def open_friend_conversation(ctx, peer_id):
    if peer_id == ctx.actor_id:
        raise missing()
    with ctx.db.begin():
        pair_id = ctx.db.scalar(
            _contact_groups(ctx.actor_id, peer_id, now_utc())
            .order_by(UserRelationship.id.desc())
            .limit(1)
        )
        if pair_id is None:
            raise missing()
        _lock_contact_group(ctx.db, pair_id)
        if not _can_send(ctx.db, ctx.actor_id, peer_id, pair_id):
            raise missing()
        row = ctx.db.scalar(
            select(ChatConversation).where(
                ChatConversation.relationship_pair_id == pair_id,
            )
        )
        if row is not None:
            if row.is_del:
                raise missing()
            return {"conversation_id": row.id}, False
        row = new_row(ChatConversation, ctx.actor_id, kind="friend", relationship_pair_id=pair_id)
        ctx.db.add(row)
        flush_unique(ctx.db)
        ctx.db.add_all(
            [
                new_row(
                    ChatConversationMember, ctx.actor_id, conversation_id=row.id, user_id=user_id
                )
                for user_id in sorted((ctx.actor_id, peer_id))
            ]
        )
        return {"conversation_id": row.id}, True


def _membership_query(actor_id, other):
    own = ChatConversationMember
    conversation = ChatConversation
    return (
        select(conversation, other.user_id)
        .join(own, own.conversation_id == conversation.id)
        .join(
            other,
            and_(
                other.conversation_id == conversation.id,
                other.user_id != actor_id,
                other.is_del.is_(False),
            ),
        )
        .where(
            own.user_id == actor_id,
            own.is_del.is_(False),
            conversation.is_del.is_(False),
            conversation.kind == "friend",
        )
    )


def _conversation(ctx, conversation_id, *, lock=False):
    stmt = _membership_query(ctx.actor_id, aliased(ChatConversationMember)).where(
        ChatConversation.id == conversation_id
    )
    if lock:
        stmt = stmt.with_for_update(of=ChatConversation).execution_options(populate_existing=True)
    row = ctx.db.execute(stmt).one_or_none()
    if row is None:
        raise missing()
    return row


def _card_names(db, ids):
    if not ids or not get_settings().benefit_enabled:
        return {}
    return dict(
        db.execute(
            public_partners()
            .with_only_columns(
                YouthPartnerProfile.id,
                YouthPartnerProfile.display_name,
            )
            .where(YouthPartnerProfile.id.in_(ids))
        ).all()
    )


def _message_view(row, names, *, summary=False):
    card = None
    if row.message_type == "partner_card":
        name = names.get(row.partner_id)
        card = {
            "is_available": name is not None,
            "partner_id": row.partner_id if name is not None else None,
            "display_name": name,
        }
    return {
        "id": row.id,
        "sequence_no": row.sequence_no,
        "type": row.message_type,
        "sender_user_id": row.sender_user_id,
        "client_message_id": row.client_message_id,
        "created_at": utc(row.created_at),
        "text": row.body_text[:120] if summary and row.body_text is not None else row.body_text,
        "partner_card": card,
    }


def send_message(ctx, conversation_id, body):
    text, partner_id = (
        (body.text, None) if isinstance(body, TextMessageInput) else (None, body.partner_id)
    )
    message = ChatMessage
    with ctx.db.begin():
        original, _ = _conversation(ctx, conversation_id)
        # 和好友解除使用相同的锁顺序，消息序号仅在会话锁内分配。
        _lock_contact_group(ctx.db, original.relationship_pair_id)
        row, peer_id = _conversation(ctx, conversation_id, lock=True)
        authorized = _can_send(ctx.db, ctx.actor_id, peer_id, row.relationship_pair_id)
        previous = ctx.db.scalar(
            select(message).where(
                message.conversation_id == row.id,
                message.sender_user_id == ctx.actor_id,
                message.client_message_id == body.client_message_id,
            )
        )
        if previous is not None:
            if previous.is_del or (
                previous.message_type,
                previous.body_text,
                previous.partner_id,
            ) != (
                body.type,
                text,
                partner_id,
            ):
                raise conflict("chat_message_id_conflict", "该消息标识已用于其他内容")
            names = _card_names(ctx.db, {partner_id}) if authorized and partner_id else {}
            return _message_view(previous, names), False
        if not authorized:
            raise conflict("chat_read_only", "好友关系已失效，仍可查看历史消息")
        names = _card_names(ctx.db, {partner_id}) if partner_id else {}
        if partner_id is not None and partner_id not in names:
            raise missing()
        now = now_utc()
        if row.last_message_at is not None:
            now = max(now, utc(row.last_message_at))
        row.last_message_seq += 1
        row.last_message_at = now
        stamp(row, ctx.actor_id)
        saved = new_row(
            message,
            ctx.actor_id,
            conversation_id=row.id,
            sender_user_id=ctx.actor_id,
            sequence_no=row.last_message_seq,
            client_message_id=body.client_message_id,
            message_type=body.type,
            body_text=text,
            partner_id=partner_id,
            created_at=now,
        )
        ctx.db.add(saved)
        flush_unique(ctx.db)
        return _message_view(saved, names), True


def list_messages(ctx, conversation_id, before, after, limit):
    if before is not None and after is not None:
        raise BenefitError(422, "chat_cursor_conflict", "before_seq 和 after_seq 只能使用一个")
    message = ChatMessage
    with ctx.db.begin():
        conversation, peer_id = _conversation(ctx, conversation_id)
        allowed = _can_send(ctx.db, ctx.actor_id, peer_id, conversation.relationship_pair_id)
        query = select(message).where(
            message.conversation_id == conversation.id, message.is_del.is_(False)
        )
        if after is not None:
            query = query.where(message.sequence_no > after).order_by(message.sequence_no)
        else:
            if before is not None:
                query = query.where(message.sequence_no < before)
            query = query.order_by(message.sequence_no.desc())
        rows = list(ctx.db.scalars(query.limit(limit + 1)))
        page = rows[:limit]
        if after is None:
            page.reverse()
        names = (
            _card_names(ctx.db, {row.partner_id for row in page if row.partner_id})
            if allowed
            else {}
        )
        return {
            "items": [_message_view(row, names) for row in page],
            "has_more": len(rows) > limit,
            "can_send": allowed,
        }


def _unread_query(conversation_id, peer_id, read_through_seq, last_seq):
    message = ChatMessage
    return (
        select(func.count())
        .select_from(message)
        .where(
            message.conversation_id == conversation_id,
            message.sender_user_id == peer_id,
            message.sequence_no > read_through_seq,
            message.sequence_no <= last_seq,
            message.is_del.is_(False),
        )
    )


def mark_read(ctx, conversation_id, read_through_seq):
    member = ChatConversationMember
    with ctx.db.begin():
        own = ctx.db.scalar(
            select(member)
            .where(
                member.conversation_id == conversation_id,
                member.user_id == ctx.actor_id,
                member.is_del.is_(False),
            )
            .with_for_update()
        )
        if own is None:
            raise missing()
        conversation, peer_id = _conversation(ctx, conversation_id)
        if read_through_seq > conversation.last_message_seq:
            raise BenefitError(422, "chat_read_out_of_range", "已读序号不能超过会话最后一条消息")
        if read_through_seq > own.last_read_seq:
            own.last_read_seq = read_through_seq
            stamp(own, ctx.actor_id)
        unread = ctx.db.scalar(
            _unread_query(
                conversation.id,
                peer_id,
                own.last_read_seq,
                conversation.last_message_seq,
            )
        )
        return {"last_read_seq": own.last_read_seq, "unread_count": unread}


def _encode_cursor(date, row_id):
    raw = f"{utc(date).isoformat()}|{row_id}".encode("ascii")
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value):
    try:
        raw = b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        date, row_id = raw.decode("ascii").rsplit("|", 1)
        parsed_date, parsed_id = datetime.fromisoformat(date), int(row_id)
        if parsed_date.tzinfo is None or not 0 < parsed_id <= 2**63 - 1:
            raise ValueError()
        return utc(parsed_date), parsed_id
    except (ValueError, OverflowError, Base64Error) as exc:
        raise BenefitError(422, "chat_cursor_invalid", "会话列表游标无效") from exc


def list_conversations(ctx, cursor, limit):
    own, message, conversation = ChatConversationMember, ChatMessage, ChatConversation
    other, peer = aliased(own), aliased(User)
    now = now_utc()
    with ctx.db.begin():
        active_pair = (
            _contact_groups(ctx.actor_id, other.user_id, now)
            .where(
                UserRelationship.relationship_pair_id == conversation.relationship_pair_id,
            )
            .correlate(conversation, other)
            .exists()
        )
        unread_count = (
            _unread_query(
                conversation.id,
                other.user_id,
                own.last_read_seq,
                conversation.last_message_seq,
            )
            .correlate(conversation, other, own)
            .scalar_subquery()
        )
        query = (
            _membership_query(ctx.actor_id, other)
            .add_columns(
                peer.display_name,
                peer.avatar_url,
                message,
                own.last_read_seq,
                unread_count,
                and_(peer.id.is_not(None), active_pair),
            )
            .outerjoin(peer, and_(peer.id == other.user_id, peer.is_del.is_(False)))
            .outerjoin(
                message,
                and_(
                    message.conversation_id == conversation.id,
                    message.sequence_no == conversation.last_message_seq,
                    message.is_del.is_(False),
                ),
            )
            .where(conversation.last_message_at.is_not(None))
        )
        if cursor is not None:
            instant, row_id = _decode_cursor(cursor)
            query = query.where(
                or_(
                    conversation.last_message_at < instant,
                    and_(conversation.last_message_at == instant, conversation.id < row_id),
                )
            )
        rows = ctx.db.execute(
            query.order_by(conversation.last_message_at.desc(), conversation.id.desc()).limit(
                limit + 1
            )
        ).all()
        page = rows[:limit]
        names = _card_names(
            ctx.db,
            {
                latest.partner_id
                for _, _, _, _, latest, _, _, allowed in page
                if allowed and latest and latest.partner_id
            },
        )
        output = []
        for row, peer_id, name, avatar, latest, read_seq, unread, allowed in page:
            output.append(
                {
                    "conversation_id": row.id,
                    "kind": row.kind,
                    "peer": {"id": peer_id, "display_name": name, "avatar_url": avatar},
                    "last_message": _message_view(latest, names if allowed else {}, summary=True)
                    if latest
                    else None,
                    "last_message_at": utc(row.last_message_at),
                    "last_read_seq": read_seq,
                    "unread_count": unread,
                    "can_send": allowed,
                }
            )
        next_cursor = (
            _encode_cursor(page[-1][0].last_message_at, page[-1][0].id)
            if len(rows) > limit
            else None
        )
        return {"items": output, "next_cursor": next_cursor}
