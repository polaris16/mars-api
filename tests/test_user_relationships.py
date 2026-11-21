"""SQLite API tests for relationships, session member grants, and contributors."""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Integer, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.database import get_db
from app.main import app
from app.models import (
    FragmentAttachment,
    MediaAsset,
    MemoryContributor,
    MemoryFragment,
    MemorySession,
    MemorySessionMember,
    Topic,
)
from app.services import user_relationship_service


@pytest.mark.parametrize("accept_before_expiry", [False, True])
def test_expired_relationship_is_listed_as_expired_and_can_be_reinvited(
    client,
    monkeypatch,
    accept_before_expiry,
):
    class Clock(datetime):
        current = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    monkeypatch.setattr(user_relationship_service, "datetime", Clock)
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    url = "/api/users/me/relationships"
    payload = {
        "related_user_id": 2,
        "relationship_type": "family",
        "relation_code": "sibling_of",
    }
    expiry = Clock.current + timedelta(hours=1)
    invitation = client.post(
        url, headers=alice, json={**payload, "valid_until": expiry.isoformat()}
    )
    assert invitation.status_code == 201, invitation.text
    action_url = f"{url}/{invitation.json()['id']}"
    if accept_before_expiry:
        assert client.post(f"{action_url}/accept", headers=bob).status_code == 200

    Clock.current = expiry
    for headers in (alice, bob):
        items = client.get(url, headers=headers).json()["items"]
        assert [item["status"] for item in items] == ["expired"]
    if not accept_before_expiry:
        for action in ("accept", "reject"):
            assert client.post(f"{action_url}/{action}", headers=bob).status_code == 409
    assert client.post(url, headers=alice, json=payload).status_code == 201


def test_invitation_expiry_is_independent_from_confirmed_relationship(client, monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    monkeypatch.setattr(user_relationship_service, "datetime", Clock)
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    payload = {"related_user_id": 2, "relationship_type": "family", "relation_code": "sibling_of"}
    invite = client.post(RELATIONSHIPS, headers=alice, json=payload).json()
    assert invite["valid_until"] is None
    assert invite["invitation_expires_at"].startswith("2026-11-26")
    Clock.current += timedelta(days=7)
    assert client.post(f"{RELATIONSHIPS}/{invite['id']}/accept", headers=bob).status_code == 409
    assert client.get(RELATIONSHIPS, headers=alice).json()["items"][0]["status"] == "expired"

    second = client.post(RELATIONSHIPS, headers=alice, json=payload)
    assert second.status_code == 201
    assert (
        client.post(f"{RELATIONSHIPS}/{second.json()['id']}/accept", headers=bob).status_code == 200
    )
    Clock.current += timedelta(days=30)
    assert client.get(RELATIONSHIPS, headers=alice).json()["items"][0]["status"] == "active"


def test_only_initiator_can_withdraw_pending_invitation_and_send_correction(client):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    payload = {"related_user_id": 2, "relationship_type": "family", "relation_code": "sibling_of"}
    invite = client.post(RELATIONSHIPS, headers=alice, json=payload).json()
    revoke_url = f"{RELATIONSHIPS}/{invite['id']}/revoke"
    assert client.post(revoke_url, headers=bob).status_code == 403
    revoked = client.post(revoke_url, headers=alice)
    assert revoked.status_code == 200 and revoked.json()[0]["status"] == "revoked"
    assert client.post(f"{RELATIONSHIPS}/{invite['id']}/accept", headers=bob).status_code == 409
    assert (
        client.post(
            RELATIONSHIPS, headers=alice, json={**payload, "relation_code": "parent_of"}
        ).status_code
        == 201
    )


@pytest.fixture
def client(monkeypatch) -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def add_btrim(connection, _):
        connection.create_function("btrim", 1, str.strip)

    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as connection:
        connection.execute(
            text("""
            CREATE TABLE t_user (
                id INTEGER PRIMARY KEY, username VARCHAR(20) NOT NULL, display_name VARCHAR(100),
                avatar_url VARCHAR(500), phone VARCHAR(20) NOT NULL, sex SMALLINT DEFAULT 0,
                birth_date DATE, tags TEXT, user_role SMALLINT DEFAULT 0, created_by BIGINT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_by BIGINT,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_del BOOLEAN NOT NULL DEFAULT 0, onboarding_completed BOOLEAN NOT NULL DEFAULT 0
            )
        """)
        )
        connection.execute(
            text("""
            CREATE TABLE t_auth_session (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id BIGINT NOT NULL,
                token_hash VARCHAR(64) NOT NULL UNIQUE, expires_at TIMESTAMP NOT NULL,
                created_by BIGINT, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_by BIGINT, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_del BOOLEAN NOT NULL DEFAULT 0
            )
        """)
        )
        connection.execute(
            text("""
            CREATE TABLE t_user_relationship (
                id INTEGER PRIMARY KEY AUTOINCREMENT, relationship_pair_id CHAR(32) NOT NULL,
                subject_user_id BIGINT NOT NULL, related_user_id BIGINT NOT NULL,
                relationship_type VARCHAR(30) NOT NULL, relation_code VARCHAR(30) NOT NULL,
                relationship_label VARCHAR(100), status VARCHAR(20) NOT NULL,
                request_message VARCHAR(200),
                authorization_method VARCHAR(30) NOT NULL, authorization_reference VARCHAR(255),
                initiated_by_user_id BIGINT NOT NULL, verified_by_user_id BIGINT,
                valid_from TIMESTAMP, valid_until TIMESTAMP, invitation_expires_at TIMESTAMP, accepted_at TIMESTAMP,
                revoked_at TIMESTAMP, revocation_reason VARCHAR(255), created_by BIGINT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_by BIGINT,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, is_del BOOLEAN NOT NULL DEFAULT 0,
                UNIQUE (relationship_pair_id, subject_user_id)
            )
        """)
        )
        connection.execute(
            text("""
            INSERT INTO t_user (id, username, display_name, phone, created_at, updated_at, is_del)
            VALUES (1, 'alice', 'Alice', '13800000001', :now, :now, 0),
                   (2, 'bob', 'Bob', '13800000002', :now, :now, 0),
                   (3, 'carol', 'Carol', '13800000003', :now, :now, 0)
        """),
            {"now": now},
        )
        for model in (
            MemorySessionMember,
            MemoryContributor,
            MemoryFragment,
            MediaAsset,
            FragmentAttachment,
        ):
            column = model.__table__.c.id
            monkeypatch.setattr(column, "type", column.type.with_variant(Integer(), "sqlite"))
        # [新增] 隔离测试中沿用实际数据库的活跃素材及关联唯一范围。
        for model in (MediaAsset, FragmentAttachment):
            for index in model.__table__.indexes:
                predicate = index.dialect_options["postgresql"].get("where")
                if predicate is not None:
                    monkeypatch.setitem(index.dialect_options["sqlite"], "where", predicate)
        MemorySession.metadata.create_all(
            connection,
            tables=[
                model.__table__
                for model in (
                    MemorySession,
                    MemorySessionMember,
                    MemoryContributor,
                    MemoryFragment,
                    MediaAsset,
                    FragmentAttachment,
                    Topic,
                )
            ],
        )
        connection.execute(
            text(
                "INSERT INTO t_memory_session (id, owner_user_id) VALUES (11, 1), (12, 1), (13, 2)"
            )
        )

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def login(client: TestClient, phone: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


MEMBERS = "/api/recording-sessions/11/members"
CONTRIBUTORS = "/api/recording-sessions/11/contributors"
RELATIONSHIPS = "/api/users/me/relationships"


def confirm_relationship(client, initiator, invitee, *, user_id, kind, code):
    invited = client.post(
        RELATIONSHIPS,
        headers=initiator,
        json={
            "related_user_id": user_id,
            "relationship_type": kind,
            "relation_code": code,
        },
    )
    assert invited.status_code == 201, invited.text
    relation_id = invited.json()["id"]
    accepted = client.post(f"{RELATIONSHIPS}/{relation_id}/accept", headers=invitee)
    assert accepted.status_code == 200, accepted.text
    return relation_id


def register_material_contributor(client, owner, relative, role):
    relation_id = confirm_relationship(
        client, owner, relative, user_id=2, kind="family", code="sibling_of"
    )
    authorized = client.post(
        MEMBERS,
        headers=owner,
        json={
            "user_id": 2,
            "role": role,
            "source_relationship_id": relation_id,
        },
    )
    assert authorized.status_code == 201, authorized.text
    registered = client.post(CONTRIBUTORS, headers=owner, json={"user_id": 2})
    assert registered.status_code == 201, registered.text
    return relation_id, authorized.json()["id"], registered.json()["id"]


@pytest.mark.parametrize("role", ["contributor", "editor"])
def test_shared_fragment_writes_check_role_consent_and_scope(client, tmp_path, monkeypatch, role):
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_mode", "mock")
    monkeypatch.setattr(settings, "object_storage_mock_directory", str(tmp_path))
    owner = login(client, "13800000001")
    relative = login(client, "13800000002")
    viewer = login(client, "13800000003")
    urls = "/api/recording-sessions"
    fragments = f"{urls}/11/fragments"
    payload = {"source_type": "text", "content_text": "Shared memory"}
    created = client.post(fragments, headers=owner, json=payload)
    assert created.status_code == 201, created.text
    original = f"{fragments}/{created.json()['id']}"
    assert client.get(fragments, headers=relative).status_code == 404
    assert (
        client.post(MEMBERS, headers=owner, json={"user_id": 3, "role": "viewer"}).status_code
        == 201
    )
    assert client.get(fragments, headers=viewer).status_code == 200
    assert client.post(fragments, headers=viewer, json=payload).status_code == 403
    assert client.get(f"{urls}/12", headers=viewer).status_code == 404
    delegate_relation = confirm_relationship(
        client, viewer, owner, user_id=1, kind="agent", code="agent_for"
    )
    (old,) = client.get(MEMBERS, headers=owner).json()["items"]
    assert client.post(f"{MEMBERS}/{old['id']}/revoke", headers=owner).status_code == 200
    delegate = client.post(
        MEMBERS,
        headers=owner,
        json={
            "user_id": 3,
            "role": "delegate",
            "source_relationship_id": delegate_relation,
            "can_manage_members": True,
        },
    )
    assert delegate.status_code == 201, delegate.text
    assert client.get(fragments, headers=viewer).status_code == 200
    assert client.post(fragments, headers=viewer, json=payload).status_code == 403
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        db.get(MemorySessionMember, delegate.json()["id"]).valid_until = datetime.now(
            timezone.utc
        ) - timedelta(minutes=1)
    assert client.get(fragments, headers=viewer).status_code == 404

    _, member_id, contributor_id = register_material_contributor(client, owner, relative, role)
    assert client.get(fragments, headers=relative).status_code == 200
    listed = client.get(urls, headers=relative, params={"page_size": 1}).json()
    assert listed["total"] == 2
    assert client.post(fragments, headers=relative, json=payload).status_code == 403
    consent_url = f"{CONTRIBUTORS}/{contributor_id}/consent"
    assert (
        client.patch(consent_url, headers=relative, json={"consent_status": "accepted"}).status_code
        == 200
    )
    own = client.post(fragments, headers=relative, json=payload)
    assert own.status_code == 201, own.text
    assert own.json()["contributor_id"] == contributor_id
    own_url = f"{fragments}/{own.json()['id']}"
    updated = {"transcript_edited": "Reviewed"}
    assert client.patch(own_url, headers=relative, json=updated).status_code == 200
    assert client.patch(original, headers=relative, json=updated).status_code == (
        200 if role == "editor" else 403
    )
    assert client.get(f"{urls}/12/fragments/{own.json()['id']}", headers=owner).status_code == 404
    assert client.delete(f"{urls}/11", headers=relative).status_code == 404
    assert (
        client.patch(
            consent_url, headers=relative, json={"consent_status": "withdrawn"}
        ).status_code
        == 200
    )
    assert client.patch(own_url, headers=relative, json=updated).status_code == 403
    assert client.get(own_url, headers=relative).status_code == 200
    assert client.post(f"{MEMBERS}/{member_id}/revoke", headers=owner).status_code == 200
    assert client.get(own_url, headers=relative).status_code == 404
    assert client.get(urls, headers=relative).json()["total"] == 1


def test_attachment_upload_completion_rechecks_session_permission(client, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_mode", "mock")
    monkeypatch.setattr(settings, "object_storage_mock_directory", str(tmp_path))
    owner = login(client, "13800000001")
    relative = login(client, "13800000002")
    created = client.post(
        "/api/recording-sessions/11/fragments",
        headers=owner,
        json={
            "source_type": "text",
            "content_text": "Attachment target",
        },
    )
    assert created.status_code == 201, created.text
    fragment_id = created.json()["id"]
    relation_id, _, contributor_id = register_material_contributor(
        client, owner, relative, "contributor"
    )
    consent_url = f"{CONTRIBUTORS}/{contributor_id}/consent"
    assert (
        client.patch(consent_url, headers=relative, json={"consent_status": "accepted"}).status_code
        == 200
    )

    def transfer(content):
        authorized = client.post(
            "/api/media-assets/upload-authorizations",
            headers=relative,
            json={
                "purpose": "attachment",
                "media_type": "image",
                "filename": "photo.png",
                "content_type": "image/png",
                "size_bytes": len(content),
                "fragment_id": fragment_id,
            },
        )
        assert authorized.status_code == 201, authorized.text
        grant = authorized.json()
        uploaded = client.put(
            grant["upload_url"], headers=grant["required_headers"], content=content
        )
        assert uploaded.status_code == 200, uploaded.text
        return grant["asset_id"]

    first_id = transfer(b"\x89PNG\r\n\x1a\nfirst")
    completed = client.post(f"/api/media-assets/{first_id}/complete", headers=relative, json={})
    assert completed.status_code == 200, completed.text
    attachments = f"/api/memory-fragments/{fragment_id}/attachments"
    added = client.post(attachments, headers=relative, json={"items": [{"asset_id": first_id}]})
    assert added.status_code == 201, added.text
    attachment_id = added.json()[0]["id"]
    assert added.json()[0]["contributor_id"] == contributor_id
    assert client.get(f"/api/media-assets/{first_id}", headers=owner).status_code == 404
    assert client.get(attachments, headers=owner).json()[0]["asset"]["id"] == first_id
    assert (
        client.patch(
            f"{attachments}/{attachment_id}", headers=owner, json={"caption": "Reviewed"}
        ).status_code
        == 200
    )
    assert client.delete(f"{attachments}/{attachment_id}", headers=owner).status_code == 200

    second_id = transfer(b"\x89PNG\r\n\x1a\nsecond")
    assert client.post(f"{RELATIONSHIPS}/{relation_id}/revoke", headers=owner).status_code == 200
    assert (
        client.post(
            f"/api/media-assets/{second_id}/complete", headers=relative, json={}
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/media-assets/{second_id}", headers=relative).json()["upload_status"]
        == "pending_upload"
    )
    assert client.get(attachments, headers=relative).status_code == 404


def test_contributor_cannot_delete_fragment_with_another_providers_attachment(
    client, tmp_path, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_mode", "mock")
    monkeypatch.setattr(settings, "object_storage_mock_directory", str(tmp_path))
    owner = login(client, "13800000001")
    relative = login(client, "13800000002")
    _, _, contributor_id = register_material_contributor(client, owner, relative, "contributor")
    assert (
        client.patch(
            f"{CONTRIBUTORS}/{contributor_id}/consent",
            headers=relative,
            json={
                "consent_status": "accepted",
            },
        ).status_code
        == 200
    )
    fragments = "/api/recording-sessions/11/fragments"
    created = client.post(
        fragments, headers=relative, json={"source_type": "text", "content_text": "Shared"}
    )
    assert created.status_code == 201, created.text
    fragment_id = created.json()["id"]
    now = datetime.now(timezone.utc)
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        db.add(
            MediaAsset(
                id=50,
                uploaded_by_user_id=1,
                purpose="attachment",
                media_type="image",
                storage_provider="mock",
                storage_region="local",
                bucket_name="test",
                object_key="owner-image",
                content_type="image/png",
                expected_size_bytes=1,
                size_bytes=1,
                content_sha256="a" * 64,
                upload_status="uploaded",
                uploaded_at=now,
                upload_expires_at=now,
            )
        )
    attachments = f"/api/memory-fragments/{fragment_id}/attachments"
    attached = client.post(attachments, headers=owner, json={"items": [{"asset_id": 50}]})
    assert attached.status_code == 201, attached.text
    attachment_url = f"{attachments}/{attached.json()[0]['id']}"
    assert client.delete(attachment_url, headers=relative).status_code == 403
    assert client.delete(f"{fragments}/{fragment_id}", headers=relative).status_code == 403
    assert client.delete(attachment_url, headers=owner).status_code == 200
    assert client.delete(f"{fragments}/{fragment_id}", headers=relative).status_code == 200


def test_relative_contributor_requires_registration_and_personal_consent(client):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    carol = login(client, "13800000003")
    target = {"user_id": 2}
    assert client.get(CONTRIBUTORS).status_code == 401
    assert client.get(CONTRIBUTORS, headers=carol).status_code == 404
    assert (
        client.post(CONTRIBUTORS, headers=alice, json={"contributor_type": "guest"}).status_code
        == 422
    )
    relation_id = confirm_relationship(
        client, alice, bob, user_id=2, kind="family", code="sibling_of"
    )
    assert client.post(CONTRIBUTORS, headers=alice, json=target).status_code == 422
    grant = client.post(
        MEMBERS,
        headers=alice,
        json={
            **target,
            "role": "contributor",
            "source_relationship_id": relation_id,
        },
    )
    assert grant.status_code == 201, grant.text
    assert client.get(f"{CONTRIBUTORS}/me", headers=bob).status_code == 404
    created = client.post(CONTRIBUTORS, headers=alice, json=target)
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["consent_status"] == "pending" and row["is_eligible"]
    assert (row["session_member_id"], row["relationship_id"]) == (grant.json()["id"], relation_id)
    assert client.get(f"{CONTRIBUTORS}/me", headers=bob).json() == row
    assert client.get(CONTRIBUTORS, headers=bob).status_code == 403

    url = f"{CONTRIBUTORS}/{row['id']}/consent"
    accepted_payload = {"consent_status": "accepted"}
    assert client.patch(url, headers=alice, json=accepted_payload).status_code == 404
    assert client.patch(url, headers=carol, json=accepted_payload).status_code == 404
    assert (
        client.patch(url.replace("/11/", "/12/"), headers=bob, json=accepted_payload).status_code
        == 404
    )
    accepted = client.patch(url, headers=bob, json=accepted_payload)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["consented_at"] is not None
    duplicate = client.post(CONTRIBUTORS, headers=alice, json=target)
    assert duplicate.status_code == 200 and duplicate.json() == accepted.json()

    assert client.post(f"{RELATIONSHIPS}/{relation_id}/revoke", headers=alice).status_code == 200
    assert not client.get(f"{CONTRIBUTORS}/me", headers=bob).json()["is_eligible"]
    assert client.post(f"{MEMBERS}/{grant.json()['id']}/revoke", headers=alice).status_code == 200
    assert client.patch(url, headers=bob, json=accepted_payload).status_code == 409
    withdrawn = client.patch(url, headers=bob, json={"consent_status": "withdrawn"})
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["consent_status"] == "withdrawn" and not withdrawn.json()["is_eligible"]
    assert withdrawn.json()["consented_at"] == accepted.json()["consented_at"]
    result = client.get(CONTRIBUTORS, headers=alice, params={"page": 1, "page_size": 1}).json()
    assert result == {"items": [withdrawn.json()], "page": 1, "page_size": 1, "total": 1}


def test_rebinding_contributor_to_new_relationship_requires_fresh_consent(client):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    first = confirm_relationship(client, alice, bob, user_id=2, kind="family", code="sibling_of")
    grant = {"user_id": 2, "role": "editor", "source_relationship_id": first}
    assert client.post(MEMBERS, headers=alice, json=grant).status_code == 201
    created = client.post(CONTRIBUTORS, headers=alice, json={"user_id": 2})
    assert created.status_code == 201, created.text
    contributor_id = created.json()["id"]
    assert (
        client.patch(
            f"{CONTRIBUTORS}/{contributor_id}/consent",
            headers=bob,
            json={
                "consent_status": "accepted",
            },
        ).status_code
        == 200
    )
    assert client.post(f"{RELATIONSHIPS}/{first}/revoke", headers=alice).status_code == 200
    second = confirm_relationship(client, alice, bob, user_id=2, kind="family", code="sibling_of")
    assert (
        client.post(
            MEMBERS, headers=alice, json={**grant, "source_relationship_id": second}
        ).status_code
        == 201
    )
    assert not client.get(f"{CONTRIBUTORS}/me", headers=bob).json()["is_eligible"]
    rebound = client.post(CONTRIBUTORS, headers=alice, json={"user_id": 2})
    assert rebound.status_code == 200, rebound.text
    row = rebound.json()
    assert (row["id"], row["relationship_id"], row["consent_status"]) == (
        contributor_id,
        second,
        "pending",
    )
    assert row["consented_at"] is None and row["is_eligible"]


def test_owner_contributor_uses_existing_record_without_exposing_phone(client):
    alice = login(client, "13800000001")
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        db.execute(text("UPDATE t_user SET display_name = NULL WHERE id = 1"))
        db.add(
            MemoryContributor(
                id=26,
                session_id=11,
                account_user_id=1,
                contributor_type="owner",
                display_name="13800000001",
                identity_status="verified",
                consent_status="pending",
            )
        )
        db.add(
            MemoryContributor(
                id=27,
                session_id=11,
                contributor_type="guest",
                display_name="Historical guest",
            )
        )
    registered = client.post(CONTRIBUTORS, headers=alice, json={"user_id": 1})
    assert registered.status_code == 200, registered.text
    assert registered.json()["id"] == 26
    assert registered.json()["display_name"] == "会话主人"
    result = client.get(CONTRIBUTORS, headers=alice).json()
    assert result["total"] == 1 and result["items"] == [registered.json()]


def test_session_member_lifecycle_is_scoped_and_regrant_preserves_member_id(client):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    carol = login(client, "13800000003")
    payload = {"user_id": 2, "role": "viewer"}
    assert client.get(MEMBERS).status_code == 401
    assert client.get(MEMBERS, headers=carol).status_code == 404
    assert client.post(MEMBERS, headers=alice, json={**payload, "user_id": 1}).status_code == 422
    created = client.post(MEMBERS, headers=alice, json=payload)
    assert created.status_code == 201, created.text
    member = created.json()
    assert member["status"] == "active" and member["is_effective"]
    assert client.get(MEMBERS, headers=bob).status_code == 403
    assert client.post(MEMBERS, headers=alice, json=payload).status_code == 409
    assert (
        client.post(
            f"/api/recording-sessions/12/members/{member['id']}/revoke", headers=alice
        ).status_code
        == 404
    )
    page = client.get(MEMBERS, headers=alice, params={"page": 2, "page_size": 1}).json()
    assert page == {"items": [], "page": 2, "page_size": 1, "total": 1}

    revoked = client.post(
        f"{MEMBERS}/{member['id']}/revoke", headers=alice, json={"reason": "access ended"}
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked" and not revoked.json()["is_effective"]
    assert revoked.json()["revocation_reason"] == "access ended"
    repeated = client.post(f"{MEMBERS}/{member['id']}/revoke", headers=alice)
    assert repeated.json() == revoked.json()
    restored = client.post(MEMBERS, headers=alice, json=payload)
    assert restored.status_code == 201, restored.text
    assert restored.json()["id"] == member["id"]
    assert restored.json()["revoked_at"] is None
    assert restored.json()["is_effective"]


def test_expired_membership_is_reported_and_can_be_regranted(client):
    alice = login(client, "13800000001")
    payload = {"user_id": 2, "role": "viewer"}
    expired = datetime.now(timezone.utc) - timedelta(days=1)
    assert (
        client.post(
            MEMBERS,
            headers=alice,
            json={
                **payload,
                "valid_until": expired.isoformat(),
            },
        ).status_code
        == 422
    )
    grant = client.post(MEMBERS, headers=alice, json=payload)
    assert grant.status_code == 201, grant.text
    member_id = grant.json()["id"]
    with contextmanager(app.dependency_overrides[get_db])() as session, session.begin():
        session.get(MemorySessionMember, member_id).valid_until = expired
    (item,) = client.get(MEMBERS, headers=alice).json()["items"]
    assert item["status"] == "expired" and not item["is_effective"]
    renewed = client.post(MEMBERS, headers=alice, json=payload)
    assert renewed.status_code == 201, renewed.text
    assert renewed.json()["id"] == member_id and renewed.json()["is_effective"]


@pytest.mark.parametrize("role", ["contributor", "editor"])
def test_family_member_grant_requires_confirmed_pair_and_stops_after_relationship_revocation(
    client, role
):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    payload = {"user_id": 2, "role": role}
    assert client.post(MEMBERS, headers=alice, json=payload).status_code == 422
    relation_id = confirm_relationship(
        client, alice, bob, user_id=2, kind="family", code="sibling_of"
    )
    assert client.get(MEMBERS, headers=alice).json()["total"] == 0
    created = client.post(
        MEMBERS, headers=alice, json={**payload, "source_relationship_id": relation_id}
    )
    assert created.status_code == 201, created.text
    assert created.json()["is_effective"]
    assert client.post(f"{RELATIONSHIPS}/{relation_id}/revoke", headers=bob).status_code == 200
    (item,) = client.get(MEMBERS, headers=alice).json()["items"]
    assert item["status"] == "active" and not item["is_effective"]


def test_delegate_can_manage_regular_members_within_explicit_authority(client):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    relation_id = confirm_relationship(
        client, bob, alice, user_id=1, kind="agent", code="agent_for"
    )
    deadline = datetime.now(timezone.utc) + timedelta(days=1)
    payload = {
        "user_id": 2,
        "role": "delegate",
        "source_relationship_id": relation_id,
        "can_manage_members": True,
        "valid_until": deadline.isoformat(),
    }
    reversed_grant = client.post(
        "/api/recording-sessions/13/members", headers=bob, json={**payload, "user_id": 1}
    )
    assert reversed_grant.status_code == 422
    delegate = client.post(MEMBERS, headers=alice, json=payload)
    assert delegate.status_code == 201, delegate.text
    delegate_id = delegate.json()["id"]
    assert client.get(MEMBERS, headers=bob).status_code == 200
    viewer = {"user_id": 3, "role": "viewer"}
    assert client.post(MEMBERS, headers=bob, json={**viewer, "role": "delegate"}).status_code == 403
    assert (
        client.post(
            MEMBERS,
            headers=bob,
            json={
                **viewer,
                "valid_until": (deadline + timedelta(days=1)).isoformat(),
            },
        ).status_code
        == 422
    )
    grant = client.post(MEMBERS, headers=bob, json=viewer)
    assert grant.status_code == 201, grant.text
    assert grant.json()["granted_by_user_id"] == 2
    assert (
        datetime.fromisoformat(grant.json()["valid_until"]).replace(tzinfo=timezone.utc) == deadline
    )
    assert client.post(f"{MEMBERS}/{delegate_id}/revoke", headers=bob).status_code == 403
    assert client.post(f"{RELATIONSHIPS}/{relation_id}/revoke", headers=alice).status_code == 200
    assert client.get(MEMBERS, headers=bob).status_code == 403


def test_relationship_list_paginates_groups_and_preserves_current_user_direction(client):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    url = "/api/users/me/relationships"
    for kind, code in (("family", "parent_of"), ("other", "other")):
        invitation = client.post(
            url,
            headers=alice,
            json={
                "related_user_id": 2,
                "relationship_type": kind,
                "relation_code": code,
            },
        )
        assert invitation.status_code == 201, invitation.text
        if kind == "family":
            assert (
                client.post(f"{url}/{invitation.json()['id']}/accept", headers=bob).status_code
                == 200
            )

    assert client.get(url, headers=bob).json()["page_size"] == 20
    for page, expected_codes in ((1, ["other"]), (2, ["child_of"]), (3, [])):
        response = client.get(url, headers=bob, params={"page": page, "page_size": 1})
        assert response.status_code == 200, response.text
        result = response.json()
        assert (result["page"], result["page_size"], result["total"]) == (page, 1, 2)
        assert [item["relation_code"] for item in result["items"]] == expected_codes

    for params in ({"page": 0}, {"page_size": 101}):
        assert client.get(url, headers=bob, params=params).status_code == 422


@pytest.mark.parametrize("legacy_case", ["missing_inverse", "wrong_inverse"])
def test_incomplete_legacy_family_relation_requires_new_confirmation(client, legacy_case):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    url = "/api/users/me/relationships"
    payload = {"related_user_id": 2, "relationship_type": "family", "relation_code": "parent_of"}
    invitation = client.post(url, headers=alice, json=payload).json()
    invitation_id = invitation["id"]
    if legacy_case == "wrong_inverse":
        assert client.post(f"{url}/{invitation_id}/accept", headers=bob).status_code == 200

    with contextmanager(app.dependency_overrides[get_db])() as session, session.begin():
        if legacy_case == "missing_inverse":
            session.execute(
                text("UPDATE t_user_relationship SET status = 'active' WHERE id = :id"),
                {"id": invitation_id},
            )
        else:
            session.execute(
                text("UPDATE t_user_relationship SET relation_code = 'sibling_of' WHERE id <> :id"),
                {"id": invitation_id},
            )

    for headers in (alice, bob):
        assert (
            client.get(url, headers=headers).json()["items"][0]["status"] == "requires_confirmation"
        )
    replacement = client.post(url, headers=alice, json=payload)
    assert replacement.status_code == 201, replacement.text
    assert client.post(f"{url}/{replacement.json()['id']}/accept", headers=bob).status_code == 200
    assert {item["status"] for item in client.get(url, headers=alice).json()["items"]} == {
        "active",
        "requires_confirmation",
    }


def test_family_invitation_acceptance_creates_reciprocal_rows_and_revoke_updates_pair(
    client: TestClient,
):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")

    created = client.post(
        "/api/users/me/relationships",
        headers=alice,
        json={
            "related_user_id": 2,
            "relationship_type": "family",
            "relation_code": "parent_of",
            "relationship_label": "女儿",
        },
    )
    assert created.status_code == 201, created.text
    invitation_id = created.json()["id"]
    assert created.json()["status"] == "pending"

    duplicate = client.post(
        "/api/users/me/relationships",
        headers=bob,
        json={
            "related_user_id": 1,
            "relationship_type": "family",
            "relation_code": "child_of",
        },
    )
    assert duplicate.status_code == 409

    accepted = client.post(f"/api/users/me/relationships/{invitation_id}/accept", headers=bob)
    assert accepted.status_code == 200, accepted.text
    pair = accepted.json()
    assert len(pair) == 2
    assert {
        (row["subject_user_id"], row["related_user_id"], row["relation_code"]) for row in pair
    } == {(1, 2, "parent_of"), (2, 1, "child_of")}
    assert len({row["relationship_pair_id"] for row in pair}) == 1

    bob_relationships = client.get("/api/users/me/relationships", headers=bob)
    assert len(bob_relationships.json()["items"]) == 1
    assert bob_relationships.json()["items"][0]["relation_code"] == "child_of"

    inverse_id = bob_relationships.json()["items"][0]["id"]
    revoked = client.post(f"/api/users/me/relationships/{inverse_id}/revoke", headers=bob)
    assert revoked.status_code == 200
    assert len(revoked.json()) == 2
    assert {row["status"] for row in revoked.json()} == {"revoked"}
    assert (
        client.post(
            f"/api/users/me/relationships/{invitation_id}/revoke", headers=alice
        ).status_code
        == 409
    )


def test_agent_relationship_stays_directional_and_reject_is_recorded(client: TestClient):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    invite = client.post(
        "/api/users/me/relationships",
        headers=alice,
        json={
            "related_user_id": 2,
            "relationship_type": "agent",
            "relation_code": "agent_for",
        },
    )
    assert invite.status_code == 201, invite.text
    rejected = client.post(f"/api/users/me/relationships/{invite.json()['id']}/reject", headers=bob)
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_relationship_invitation_rejects_mismatched_code_and_unauthorized_accept(
    client: TestClient,
):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    invalid = client.post(
        "/api/users/me/relationships",
        headers=alice,
        json={
            "related_user_id": 2,
            "relationship_type": "family",
            "relation_code": "agent_for",
        },
    )
    assert invalid.status_code == 422

    invite = client.post(
        "/api/users/me/relationships",
        headers=alice,
        json={
            "related_user_id": 2,
            "relationship_type": "family",
            "relation_code": "sibling_of",
        },
    )
    assert invite.status_code == 201
    unauthorized = client.post(
        f"/api/users/me/relationships/{invite.json()['id']}/accept", headers=alice
    )
    assert unauthorized.status_code == 404
