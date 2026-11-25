"""[新增] 通过真实 Bearer 入口验证好友聊天和历史记录。"""

from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Integer

from app.core.config import get_settings
from app.db.database import get_db
from app.main import app
from app.models import (
    Base,
    BenefitStaff,
    ChatConversation,
    ChatConversationMember,
    ChatMessage,
    YouthPartnerProfile,
)
from app.services import chat_service
from app.services.benefit_common import now_utc
from test_benefit_flows import benefits
from test_user_relationships import RELATIONSHIPS, client, confirm_relationship, login

CHAT = "/api/users/me/conversations"


@pytest.fixture
def chat(benefits, monkeypatch):
    client, _ = benefits
    with contextmanager(app.dependency_overrides[get_db])() as db:
        engine = db.get_bind()
    with engine.begin() as conn:
        driver = conn.connection.driver_connection
        driver.create_function("char_length", 1, lambda value: len(value) if value else 0)
        driver.create_function("btrim", 1, lambda value: value.strip(" ") if value else value)
        for table in (ChatConversation.__table__, ChatMessage.__table__):
            monkeypatch.setattr(
                table.c.id, "type", table.c.id.type.with_variant(Integer(), "sqlite")
            )
        Base.metadata.create_all(
            conn,
            tables=[
                ChatConversation.__table__,
                ChatConversationMember.__table__,
                ChatMessage.__table__,
            ],
        )
    actors = [login(client, f"1380000000{i}") for i in (1, 2, 3)]
    return client, *actors


def friend(client, source, target, user_id=2):
    return confirm_relationship(
        client, source, target, user_id=user_id, kind="contact", code="contact_of"
    )


def request(client, method, path, actor, body=None, *, expected=200):
    reply = client.request(method, path, headers=actor, json=body)
    assert reply.status_code == expected, reply.text
    return reply.json()


def open_chat(client, actor, user_id, *, expected=201):
    return request(
        client,
        "POST",
        f"{CHAT}/friends",
        actor,
        {"contact_user_id": user_id},
        expected=expected,
    )["conversation_id"]


def send_text(client, path, actor, text, *, key=None):
    return request(
        client,
        "POST",
        path,
        actor,
        {
            "type": "text",
            "client_message_id": str(key or uuid4()),
            "text": text,
        },
        expected=201,
    )


def test_chat_keeps_order_while_paging_and_tracks_read_progress(chat, monkeypatch):
    client, alice, bob, carol = chat
    monkeypatch.setattr(get_settings(), "benefit_enabled", False)
    request(client, "POST", f"{CHAT}/friends", alice, {"contact_user_id": 2}, expected=404)
    friend(client, alice, bob)
    chat_id = open_chat(client, alice, 2)
    assert open_chat(client, bob, 1, expected=200) == chat_id
    assert request(client, "GET", CHAT, alice)["items"] == []
    path, read_path = f"{CHAT}/{chat_id}/messages", f"{CHAT}/{chat_id}/read"
    assert request(client, "GET", path, bob) == {"items": [], "has_more": False, "can_send": True}
    request(client, "GET", path, {}, expected=401)
    request(client, "GET", path, carol, expected=404)
    request(client, "PUT", read_path, carol, {"read_through_seq": 0}, expected=404)
    request(
        client,
        "POST",
        path,
        carol,
        {
            "type": "text",
            "client_message_id": str(uuid4()),
            "text": "外部文本",
        },
        expected=404,
    )
    key = uuid4()
    first = send_text(client, path, alice, " 第一条 ", key=key)
    assert first["text"] == "第一条"
    body = {"type": "text", "client_message_id": str(key), "text": "第一条"}
    assert request(client, "POST", path, alice, body) == first
    request(client, "POST", path, alice, {**body, "text": "其他内容"}, expected=409)
    invalid = client.post(path, headers=alice, json={**body, "text": "protected\u0000private"})
    assert invalid.status_code == 422 and "protected" not in invalid.text
    for seq in range(2, 36):
        saved = send_text(client, path, alice if seq % 2 else bob, f"消息 {seq}")
        assert saved["sequence_no"] == seq
    listing = request(client, "GET", CHAT, bob)["items"][0]
    assert listing["peer"]["id"] == 1 and listing["unread_count"] == 18
    latest = request(client, "GET", path, bob)
    assert latest["has_more"]
    assert [item["sequence_no"] for item in latest["items"]] == list(range(6, 36))
    send_text(client, path, alice, "刚刚发出的消息")
    earlier = request(client, "GET", f"{path}?before_seq=6", bob)
    assert [item["sequence_no"] for item in earlier["items"]] == list(range(1, 6))
    assert not earlier["has_more"]
    new = request(client, "GET", f"{path}?after_seq=34&limit=1", bob)
    assert new["has_more"] and new["items"][0]["sequence_no"] == 35
    new = request(client, "GET", f"{path}?after_seq=35", bob)
    assert not new["has_more"] and new["items"][0]["sequence_no"] == 36
    request(client, "GET", f"{path}?after_seq=1&before_seq=10", bob, expected=422)
    assert request(client, "GET", CHAT, bob)["items"][0]["unread_count"] == 19
    assert request(client, "PUT", read_path, bob, {"read_through_seq": 35}) == {
        "last_read_seq": 35,
        "unread_count": 1,
    }
    assert request(client, "PUT", read_path, bob, {"read_through_seq": 4})["last_read_seq"] == 35
    request(client, "PUT", read_path, bob, {"read_through_seq": 37}, expected=422)
    assert request(client, "PUT", read_path, bob, {"read_through_seq": 36})["unread_count"] == 0
    assert request(client, "GET", CHAT, alice)["items"][0]["unread_count"] == 17
    assert client.get(path, headers=bob).headers["Cache-Control"] == "no-store"


def test_revoke_preserves_history_and_disables_partner_cards(chat):
    client, alice, bob, _ = chat
    relation_id = friend(client, alice, bob)
    chat_id = open_chat(client, alice, 2)
    path = f"{CHAT}/{chat_id}/messages"
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        partner = YouthPartnerProfile(user_id=3, display_name="青年伙伴", status="approved")
        staff = BenefitStaff(user_id=3, role="provider", status="active")
        db.add_all([partner, staff])
        db.flush()
        partner_id = partner.id
    send_text(client, path, alice, "你看看这位伙伴")
    card_body = {
        "type": "partner_card",
        "client_message_id": str(uuid4()),
        "partner_id": partner_id,
    }
    card = request(client, "POST", path, alice, card_body, expected=201)["partner_card"]
    assert card == {"is_available": True, "partner_id": partner_id, "display_name": "青年伙伴"}
    assert request(client, "GET", CHAT, bob)["items"][0]["last_message"]["partner_card"] == card
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        db.get(YouthPartnerProfile, partner_id).status = "disabled"
    hidden = {"is_available": False, "partner_id": None, "display_name": None}
    assert request(client, "GET", path, bob)["items"][-1]["partner_card"] == hidden
    request(
        client, "POST", path, alice, {**card_body, "client_message_id": str(uuid4())}, expected=404
    )
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        db.get(YouthPartnerProfile, partner_id).status = "approved"
    request(client, "POST", f"{RELATIONSHIPS}/{relation_id}/revoke", bob)
    archived = request(client, "GET", path, alice)
    assert not archived["can_send"] and archived["items"][0]["text"] == "你看看这位伙伴"
    assert archived["items"][-1]["partner_card"] == hidden
    assert request(client, "POST", path, alice, card_body)["partner_card"] == hidden
    denied = request(
        client,
        "POST",
        path,
        bob,
        {
            "type": "text",
            "text": "新消息",
            "client_message_id": str(uuid4()),
        },
        expected=409,
    )
    assert denied["code"] == "chat_read_only"
    friend(client, alice, bob)
    fresh = open_chat(client, alice, 2)
    assert fresh != chat_id
    assert request(client, "GET", f"{CHAT}/{fresh}/messages", bob)["items"] == []
    assert not request(client, "GET", path, bob)["can_send"]


def test_inbox_cursor_breaks_timestamp_ties(chat, monkeypatch):
    client, alice, bob, carol = chat
    friend(client, alice, bob)
    friend(client, alice, carol, user_id=3)
    first = open_chat(client, alice, 2)
    second = open_chat(client, alice, 3)
    now = now_utc() + timedelta(seconds=1)
    monkeypatch.setattr(chat_service, "now_utc", lambda: now)
    for conversation_id in (first, second):
        send_text(client, f"{CHAT}/{conversation_id}/messages", alice, "你好")
    first_page = request(client, "GET", f"{CHAT}?limit=1", alice)
    assert first_page["items"][0]["conversation_id"] == second
    next_page = request(client, "GET", f"{CHAT}?limit=1&cursor={first_page['next_cursor']}", alice)
    assert next_page["items"][0]["conversation_id"] == first and next_page["next_cursor"] is None
    request(client, "GET", f"{CHAT}?cursor=***", alice, expected=422)
