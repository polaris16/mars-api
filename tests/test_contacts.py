"""[新增] 通过 HTTP 验证联系人申请、资料可见性和记忆授权。"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import select

from app.db.database import get_db
from app.main import app
from app.models import MemorySessionMember, User, UserRelationship
from app.services import user_relationship_service
from test_memory_extraction import (
    FRAGMENTS,
    OUTSIDER,
    OWNER,
    SESSION,
    VIEWER,
    extraction_api,
    save_text,
)
from test_user_relationships import RELATIONSHIPS, client, confirm_relationship, login

CONTACTS = "/api/users/me/contacts"
CONTACT_SEARCH = f"{CONTACTS}/search"
CONTACT_REQUESTS = f"{CONTACTS}/requests"


# [新增] 旧入口的有效期可以缩短等待时间，接受后好友双方均无期限。
def test_confirmed_contacts_outlive_invitation_deadline(client, monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 11, 24, 8, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    monkeypatch.setattr(user_relationship_service, "datetime", Clock)
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    deadline = Clock.current + timedelta(hours=1)
    pending = client.post(
        RELATIONSHIPS,
        headers=alice,
        json={
            "related_user_id": 2,
            "relationship_type": "contact",
            "relation_code": "contact_of",
            "valid_until": deadline.isoformat(),
        },
    )
    assert pending.status_code == 201, pending.text
    row = pending.json()
    assert row["invitation_expires_at"] is not None
    accepted = client.post(f"{RELATIONSHIPS}/{row['id']}/accept", headers=bob)
    assert accepted.status_code == 200, accepted.text
    assert len(accepted.json()) == 2
    for member in accepted.json():
        assert member["invitation_expires_at"] is None
        assert member["valid_until"] is None
        assert member["accepted_at"] is not None
    Clock.current = deadline + timedelta(days=8)
    assert client.get(CONTACTS, headers=alice).json()["total"] == 1
    assert client.get(CONTACTS, headers=bob).json()["total"] == 1
    history = client.get(f"{CONTACT_REQUESTS}?direction=outgoing", headers=alice).json()
    assert history["items"][0]["status"] == "accepted"
    revoked = client.post(f"{RELATIONSHIPS}/{row['id']}/revoke", headers=alice)
    assert revoked.status_code == 200, revoked.text
    assert client.get(CONTACTS, headers=bob).json()["total"] == 0


# [新增] 执行历史数据迁移，好友恢复时不得延长记忆授权。
def test_permanent_contacts_migration_preserves_grant_deadline(client):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    relation_id = confirm_relationship(
        client, alice, bob, user_id=2, kind="contact", code="contact_of"
    )
    deadline = datetime.now(timezone.utc) - timedelta(days=1)
    migration = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("20261124_01")
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        original = db.get(UserRelationship, relation_id)
        original.valid_until = deadline
        original.invitation_expires_at = deadline
        db.add(MemorySessionMember(
            session_id=11, user_id=2, role="viewer", status="active",
            source_relationship_id=relation_id, granted_by_user_id=1,
        ))
        db.flush()
        with Operations.context(MigrationContext.configure(db.connection())):
            migration.module.upgrade()
        db.expire_all()
        assert db.scalar(select(MemorySessionMember)).valid_until == deadline.replace(tzinfo=None)
        assert original.valid_until is None and original.invitation_expires_at is None
    assert client.get(CONTACTS, headers=alice).json()["total"] == 1

# [新增] 两种列表均支持与分页结果一致的全局排序，并可一次返回超过 100 项。
def test_friend_and_relationship_lists_support_sorted_full_results(client):
    alice = login(client, "13800000001")
    outsider = login(client, "13800000003")
    names = {user_id: f"contact {user_id}" for user_id in range(100, 201)}
    names.update({100: "Zebra", 101: "  apple  ", 102: "Banana", 103: " ", 104: None})
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        for user_id, name in names.items():
            db.add(
                User(
                    id=user_id, username=f"user{user_id}", phone=f"139{user_id}", display_name=name
                )
            )
            pair_id = uuid.uuid4()
            for subject, related in ((1, user_id), (user_id, 1)):
                db.add(
                    UserRelationship(
                        relationship_pair_id=pair_id,
                        subject_user_id=subject,
                        related_user_id=related,
                        relationship_type="contact",
                        relation_code="contact_of",
                        status="active",
                        authorization_method="in_app",
                        initiated_by_user_id=1,
                    )
                )

    for path in (CONTACTS, RELATIONSHIPS):
        assert (
            client.get(path, headers=alice).json()
            == client.get(
                path, headers=alice, params={"sort": "default", "paginate": "true"}
            ).json()
        )
        for sort, first_ids in (("name_asc", [101, 102]), ("name_desc", [100, 200])):
            response = client.get(
                path,
                headers=alice,
                params={"paginate": "false", "sort": sort, "page": 9, "page_size": 1},
            )
            assert response.status_code == 200, response.text
            result = response.json()
            assert (result["page"], result["page_size"], result["total"]) == (1, 101, 101)
            ids = [item["related_user_id"] for item in result["items"]]
            assert ids[:2] == first_ids
            assert set(ids[-2:]) == {103, 104}
            assert set(ids) == set(names)

            response = client.get(
                path, headers=alice, params={"sort": sort, "page": 2, "page_size": 1}
            )
            result = response.json()
            assert (result["page"], result["page_size"], result["total"]) == (2, 1, 101)
            assert [item["related_user_id"] for item in result["items"]] == first_ids[1:]

        assert client.get(path, headers=outsider, params={"paginate": "false"}).json() == {
            "items": [],
            "page": 1,
            "page_size": 0,
            "total": 0,
        }
        assert client.get(path, headers=alice, params={"sort": "unsupported"}).status_code == 422

    filtered = client.get(
        CONTACTS, headers=alice, params={"paginate": "false", "keyword": "apple"}
    ).json()
    assert [item["related_user_id"] for item in filtered["items"]] == [101]
    assert filtered["total"] == filtered["page_size"] == 1
    for view in ("incoming", "outgoing"):
        for params in ({"paginate": "false"}, {"sort": "name_asc"}):
            response = client.get(CONTACTS, headers=alice, params={"view": view, **params})
            assert response.status_code == 422, response.text


# [新增] 输入变化时可按精确手机号或部分昵称重新查询同一入口。
def test_candidate_search_filters_relationships_and_returns_only_profiles(client):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    login(client, "13800000003")
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        db.get(User, 3).display_name = "Bowie"
        db.get(User, 2).avatar_url = "https://example.test/bob.jpg"

    def search(keyword, **page):
        reply = client.post(CONTACT_SEARCH, headers=alice, json={"keyword": keyword, **page})
        assert reply.status_code == 200, reply.text
        return reply.json()

    assert client.post(CONTACT_SEARCH, json={"keyword": "Bob"}).status_code == 401
    assert search(" ")["items"] == []
    assert search("B")["items"] == []
    assert search("Ali")["items"] == []
    assert search("1380000000")["items"] == []
    assert search("%_")["items"] == []
    assert search(" 13800000002 ")["items"] == [
        {"id": 2, "display_name": "Bob", "avatar_url": "https://example.test/bob.jpg"}
    ]
    first = search("bo", page_size=1)
    assert first["total"] == 2 and first["items"][0]["id"] == 2
    assert search("BO", page=2, page_size=1)["items"][0]["id"] == 3
    # [新增] 倒序先于分页，候选搜索继续限制全量查询。
    assert search("BO", sort="name_desc", page_size=1)["items"][0]["id"] == 3
    assert search("BO", sort="name_desc", page=2, page_size=1)["items"][0]["id"] == 2
    for options in ({"paginate": False}, {"sort": "unknown"}):
        assert (
            client.post(
                CONTACT_SEARCH, headers=alice, json={"keyword": "Bo", **options}
            ).status_code
            == 422
        )
    assert search("BOW")["items"][0]["id"] == 3
    assert (
        client.post(
            CONTACT_SEARCH, headers=alice, json={"keyword": "Bo", "page_size": 21}
        ).status_code
        == 422
    )

    confirm_relationship(client, alice, bob, user_id=2, kind="family", code="sibling_of")
    assert search("bo")["total"] == 2
    reverse = client.post(
        CONTACT_REQUESTS, headers=bob, json={"related_user_id": 1, "message": "你好"}
    )
    assert reverse.status_code == 201, reverse.text
    assert search("BO")["total"] == 1
    assert search("13800000002")["total"] == 0
    assert (
        client.post(f"{RELATIONSHIPS}/{reverse.json()['id']}/accept", headers=alice).status_code
        == 200
    )
    assert search("Bob")["items"] == []
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        db.get(User, 3).is_del = True
    assert search("Bowie")["total"] == 0
    stale = client.post(
        CONTACT_REQUESTS, headers=alice, json={"related_user_id": 3, "message": "你好"}
    )
    assert stale.status_code == 404


def test_new_contact_requests_show_messages_and_rejections_in_history(client):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    carol = login(client, "13800000003")
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        db.get(User, 1).avatar_url = "https://example.test/alice.jpg"
    assert client.get(CONTACT_REQUESTS, params={"direction": "incoming"}).status_code == 401
    assert client.get(CONTACT_REQUESTS, headers=bob).status_code == 422
    for invalid in (
        {"related_user_id": 2},
        {"related_user_id": 2, "message": "   "},
        {"related_user_id": 2, "message": "字" * 201},
        {"related_user_id": 2, "message": "\x00"},
        {"related_user_id": 2**63, "message": "你好"},
        {"related_user_id": 2, "code": "3.1795069123." + "a" * 64, "message": "你好"},
    ):
        assert client.post(CONTACT_REQUESTS, headers=alice, json=invalid).status_code == 422
    assert (
        client.post(
            CONTACT_REQUESTS, headers=alice, json={"related_user_id": 1, "message": "你好"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            CONTACT_REQUESTS, headers=alice, json={"related_user_id": 100, "message": "你好"}
        ).status_code
        == 404
    )

    message = {"related_user_id": 2, "message": " 我是 Alice\n想和您聊聊 "}
    reply = client.post(CONTACT_REQUESTS, headers=alice, json=message)
    assert reply.status_code == 201, reply.text
    request_id = reply.json()["id"]
    assert client.post(CONTACT_REQUESTS, headers=alice, json=message).status_code == 409
    assert (
        client.post(
            CONTACT_REQUESTS, headers=bob, json={"related_user_id": 1, "message": "我是 Bob"}
        ).status_code
        == 409
    )
    another = client.post(
        CONTACT_REQUESTS, headers=carol, json={"related_user_id": 2, "message": "我是 Carol"}
    )
    assert another.status_code == 201, another.text
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        first = db.get(UserRelationship, request_id)
        first.created_at = first.created_at - timedelta(days=1)

    common = {"direction": "incoming", "page_size": 1}
    page = client.get(CONTACT_REQUESTS, headers=bob, params=common).json()
    assert page["total"] == 2 and page["items"][0]["request_id"] == another.json()["id"]
    # [新增] 申请按时间升序后切页，不能关闭分页。
    ascending = client.get(
        CONTACT_REQUESTS, headers=bob, params={**common, "sort": "created_at_asc"}
    ).json()
    assert ascending["items"][0]["request_id"] == request_id
    assert (
        client.get(
            CONTACT_REQUESTS, headers=bob, params={**common, "paginate": "false"}
        ).status_code
        == 422
    )
    item = client.get(CONTACT_REQUESTS, headers=bob, params={**common, "page": 2}).json()["items"][
        0
    ]
    assert item["request_id"] == request_id and item["message"] == "我是 Alice\n想和您聊聊"
    assert item["status"] == "pending" and item["created_at"] and item["resolved_at"] is None
    assert item["contact_user"] == {
        "id": 1,
        "display_name": "Alice",
        "avatar_url": "https://example.test/alice.jpg",
    }
    assert client.get(CONTACT_REQUESTS, headers=carol, params=common).json()["total"] == 0
    action = f"{RELATIONSHIPS}/{request_id}/reject"
    assert client.post(action, headers=carol).status_code == 404
    assert client.post(action, headers=alice).status_code == 404
    assert client.post(action, headers=bob).status_code == 200
    assert client.post(action, headers=bob).status_code == 409
    sent = client.get(
        CONTACT_REQUESTS, headers=alice, params={"direction": "outgoing", "status": "rejected"}
    ).json()
    (rejected,) = sent["items"]
    assert sent["total"] == 1 and rejected["request_id"] == request_id
    assert rejected["resolved_at"] and rejected["status"] == "rejected"
    assert rejected["message"] == item["message"] and rejected["contact_user"]["id"] == 2
    filtered = client.get(CONTACT_REQUESTS, headers=bob, params={**common, "status": "rejected"})
    assert filtered.json()["items"][0]["request_id"] == request_id
    assert (
        client.get(CONTACT_REQUESTS, headers=bob, params={**common, "status": "pending"}).json()[
            "total"
        ]
        == 1
    )
    assert client.get(CONTACTS, headers=alice).json()["total"] == 0


def test_contact_history_keeps_original_result_after_relationship_revocation(client):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    login(client, "13800000003")
    invite = client.post(
        CONTACT_REQUESTS, headers=alice, json={"related_user_id": 2, "message": "老同学"}
    )
    assert invite.status_code == 201, invite.text
    request_id = invite.json()["id"]
    accepted = client.post(f"{RELATIONSHIPS}/{request_id}/accept", headers=bob)
    assert accepted.status_code == 200, accepted.text
    original, inverse = accepted.json()
    with contextmanager(app.dependency_overrides[get_db])() as db:
        assert db.get(UserRelationship, original["id"]).request_message == "老同学"
        assert db.get(UserRelationship, inverse["id"]).request_message is None

    outgoing = {"direction": "outgoing"}
    first = client.get(CONTACT_REQUESTS, headers=alice, params=outgoing).json()
    (kept,) = first["items"]
    assert kept["status"] == "accepted" and kept["resolved_at"]
    assert client.get(CONTACT_REQUESTS, headers=bob, params=outgoing).json()["total"] == 0
    assert client.post(f"{RELATIONSHIPS}/{inverse['id']}/revoke", headers=bob).status_code == 200
    assert client.get(CONTACTS, headers=alice).json()["total"] == 0
    assert client.get(CONTACT_REQUESTS, headers=alice, params=outgoing).json() == first

    withdrawn = client.post(
        CONTACT_REQUESTS, headers=alice, json={"related_user_id": 3, "message": "您好"}
    )
    assert withdrawn.status_code == 201, withdrawn.text
    assert (
        client.post(f"{RELATIONSHIPS}/{withdrawn.json()['id']}/revoke", headers=alice).status_code
        == 200
    )
    row = client.get(
        CONTACT_REQUESTS, headers=alice, params={**outgoing, "status": "withdrawn"}
    ).json()["items"][0]
    assert row["request_id"] == withdrawn.json()["id"] and row["resolved_at"]

    expired = client.post(
        CONTACT_REQUESTS, headers=alice, json={"related_user_id": 3, "message": "又见面了"}
    )
    assert expired.status_code == 201, expired.text
    expired_id = expired.json()["id"]
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        db.get(UserRelationship, expired_id).invitation_expires_at = datetime.now(
            timezone.utc
        ) - timedelta(seconds=1)
    row = client.get(
        CONTACT_REQUESTS, headers=alice, params={**outgoing, "status": "expired"}
    ).json()["items"][0]
    assert row["request_id"] == expired_id and row["resolved_at"] is None
    found = client.post(CONTACT_SEARCH, headers=alice, json={"keyword": "Carol"})
    assert [item["id"] for item in found.json()["items"]] == [3]
    assert (
        client.get(
            CONTACT_REQUESTS, headers=alice, params={**outgoing, "status": "pending"}
        ).json()["total"]
        == 0
    )


def test_invite_code_and_contact_lists_follow_both_accounts(client):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    carol = login(client, "13800000003")
    assert client.get(CONTACTS).status_code == 401
    assert client.post(f"{CONTACTS}/invite-code").status_code == 401
    invite = client.post(f"{CONTACTS}/invite-code", headers=bob)
    assert invite.status_code == 200, invite.text
    assert invite.headers["Cache-Control"] == "no-store"
    code = invite.json()["code"]
    assert bob["Authorization"].split()[-1] not in code
    requests = f"{CONTACTS}/requests"
    invalid = code[:-1] + ("0" if code[-1] != "0" else "1")
    assert client.post(requests, headers=alice, json={"code": invalid}).status_code == 404
    assert client.post(requests, headers=bob, json={"code": code}).status_code == 422

    created = client.post(requests, headers=alice, json={"code": code})
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["status"] == "pending"
    assert row["relationship_type"] == "contact" and row["relation_code"] == "contact_of"
    assert client.post(requests, headers=alice, json={"code": code}).status_code == 409
    assert client.get(CONTACTS, headers=alice).json()["total"] == 0
    assert client.get(CONTACTS, headers=carol, params={"view": "incoming"}).json()["total"] == 0
    incoming = client.get(CONTACTS, headers=bob, params={"view": "incoming"}).json()
    outgoing = client.get(CONTACTS, headers=alice, params={"view": "outgoing"}).json()
    assert incoming["total"] == outgoing["total"] == 1
    assert incoming["items"][0]["contact_user"] == {
        "id": 1,
        "display_name": "Alice",
        "avatar_url": None,
    }
    assert outgoing["items"][0]["contact_user"]["id"] == 2
    legacy_message = client.get(
        CONTACT_REQUESTS, headers=bob, params={"direction": "incoming"}
    ).json()["items"][0]
    assert legacy_message["request_id"] == row["id"] and legacy_message["message"] is None
    assert client.post(f"{RELATIONSHIPS}/{row['id']}/accept", headers=carol).status_code == 404
    accepted = client.post(f"{RELATIONSHIPS}/{row['id']}/accept", headers=bob)
    assert accepted.status_code == 200, accepted.text
    first, inverse = accepted.json()
    assert first["relationship_pair_id"] == inverse["relationship_pair_id"]
    assert inverse["relation_code"] == "contact_of"
    assert (inverse["subject_user_id"], inverse["related_user_id"]) == (2, 1)
    assert client.get(CONTACTS, headers=bob, params={"view": "incoming"}).json()["total"] == 0
    visible = client.get(CONTACTS, headers=bob, params={"keyword": "ali", "page_size": 1}).json()
    assert visible["total"] == 1 and visible["items"][0]["id"] == inverse["id"]
    assert client.get(CONTACTS, headers=bob, params={"keyword": "%"}).json()["total"] == 0
    assert (
        client.get(CONTACTS, headers=bob, params={"page": 2, "page_size": 1}).json()["items"] == []
    )

    # 已确认关系仍独立有效，退出只撤销此登录签发的分享码。
    assert client.post("/api/auth/logout", headers=bob).status_code == 200
    assert client.post(requests, headers=carol, json={"code": code}).status_code == 404
    assert client.get(CONTACTS, headers=alice).json()["total"] == 1
    undone = client.post(f"{RELATIONSHIPS}/{first['id']}/revoke", headers=alice)
    assert undone.status_code == 200, undone.text
    assert {item["status"] for item in undone.json()} == {"revoked"}
    assert client.get(CONTACTS, headers=alice).json()["total"] == 0


def test_contact_list_excludes_expired_requests_and_non_contacts(client):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    confirm_relationship(client, alice, bob, user_id=2, kind="family", code="sibling_of")
    payload = {"related_user_id": 2, "relationship_type": "contact", "relation_code": "contact_of"}
    invited = client.post(RELATIONSHIPS, headers=alice, json=payload)
    assert invited.status_code == 201, invited.text
    assert (
        client.post(
            RELATIONSHIPS,
            headers=alice,
            json={**payload, "relation_code": "related_to"},
        ).status_code
        == 422
    )
    assert client.get(CONTACTS, headers=alice).json()["total"] == 0
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        db.get(UserRelationship, invited.json()["id"]).invitation_expires_at = datetime.now(
            timezone.utc
        ) - timedelta(seconds=1)
    assert client.get(CONTACTS, headers=bob, params={"view": "incoming"}).json()["total"] == 0
    assert (
        client.post(f"{RELATIONSHIPS}/{invited.json()['id']}/accept", headers=bob).status_code
        == 409
    )
    assert client.post(RELATIONSHIPS, headers=alice, json=payload).status_code == 201


def test_contact_contributes_own_attachments_only_with_current_grant(extraction_api):
    client, factory = extraction_api
    members = f"{SESSION}/members"
    assert client.patch(SESSION, headers=OWNER, json={"visibility": "public"}).status_code == 200
    record = save_text(client, "我搬了家。")
    fragment_id = record["id"]
    original = f"{FRAGMENTS}/{fragment_id}"
    attachments = f"/api/memory-fragments/{fragment_id}/attachments"
    relation_id = confirm_relationship(
        client, OWNER, VIEWER, user_id=2, kind="contact", code="contact_of"
    )
    assert client.get(SESSION, headers=VIEWER).status_code == 404
    assert client.get("/api/recording-sessions", headers=VIEWER).json()["total"] == 0
    grant = {"user_id": 2, "source_relationship_id": relation_id}
    assert client.post(members, headers=OWNER, json={**grant, "role": "editor"}).status_code == 422
    assert (
        client.post(
            "/api/recording-sessions/12/members",
            headers=OWNER,
            json={**grant, "role": "contributor"},
        ).status_code
        == 422
    )
    read = client.post(members, headers=OWNER, json={**grant, "role": "viewer"})
    assert read.status_code == 201, read.text
    assert client.get(original, headers=VIEWER).status_code == 200
    assert client.get(SESSION, headers=OUTSIDER).status_code == 404
    assert client.post(f"{members}/{read.json()['id']}/revoke", headers=OWNER).status_code == 200
    assert client.get(original, headers=VIEWER).status_code == 404

    added = client.post(members, headers=OWNER, json={**grant, "role": "contributor"})
    assert added.status_code == 201, added.text
    assert client.get(original, headers=VIEWER).status_code == 200
    contributor = client.post(f"{SESSION}/contributors", headers=OWNER, json={"user_id": 2})
    assert contributor.status_code == 201, contributor.text
    consent = f"{SESSION}/contributors/{contributor.json()['id']}/consent"
    raw = b"\x89PNG\r\n\x1a\nphoto"
    upload_url = "/api/media-assets/upload-authorizations"
    request = {
        "purpose": "attachment",
        "media_type": "image",
        "filename": "photo.png",
        "content_type": "image/png",
        "size_bytes": len(raw),
        "fragment_id": fragment_id,
    }
    assert client.post(upload_url, headers=VIEWER, json=request).status_code == 403
    assert (
        client.patch(consent, headers=VIEWER, json={"consent_status": "accepted"}).status_code
        == 200
    )
    assert (
        client.post(
            FRAGMENTS,
            headers=VIEWER,
            json={
                "source_type": "text",
                "content_text": "不允许写原始片段",
            },
        ).status_code
        == 403
    )
    assert (
        client.patch(original, headers=VIEWER, json={"transcript_edited": "修改"}).status_code
        == 403
    )
    assert client.delete(original, headers=VIEWER).status_code == 403

    def upload_for(headers, content):
        reply = client.post(
            upload_url, headers=headers, json={**request, "size_bytes": len(content)}
        )
        assert reply.status_code == 201, reply.text
        signed = reply.json()
        assert (
            client.put(
                signed["upload_url"], headers=signed["required_headers"], content=content
            ).status_code
            == 200
        )
        return signed["asset_id"]

    def attach(headers, content):
        asset_id = upload_for(headers, content)
        assert (
            client.post(
                f"/api/media-assets/{asset_id}/complete", headers=headers, json={}
            ).status_code
            == 200
        )
        linked = client.post(attachments, headers=headers, json={"items": [{"asset_id": asset_id}]})
        assert linked.status_code == 201, linked.text
        return f"{attachments}/{linked.json()[0]['id']}"

    owned = attach(OWNER, raw)
    contributed = attach(VIEWER, raw + b"-friend")
    assert client.patch(owned, headers=VIEWER, json={"caption": "禁止"}).status_code == 403
    assert client.delete(owned, headers=VIEWER).status_code == 403
    assert client.patch(contributed, headers=VIEWER, json={"caption": "地点"}).status_code == 200
    assert client.delete(contributed, headers=VIEWER).status_code == 200
    retained = attach(VIEWER, raw + b"-retained")
    pending_asset = upload_for(VIEWER, raw + b"-pending")

    with factory() as db:
        reverse_id = db.scalar(
            select(UserRelationship.id).where(
                UserRelationship.subject_user_id == 2,
                UserRelationship.relationship_type == "contact",
            )
        )
    assert client.post(f"{RELATIONSHIPS}/{reverse_id}/revoke", headers=VIEWER).status_code == 200
    assert client.get(original, headers=VIEWER).status_code == 404
    assert client.patch(retained, headers=VIEWER, json={"caption": "失效"}).status_code == 404
    assert (
        client.post(
            f"/api/media-assets/{pending_asset}/complete", headers=VIEWER, json={}
        ).status_code
        == 404
    )
    assert len(client.get(attachments, headers=OWNER).json()) == 2
