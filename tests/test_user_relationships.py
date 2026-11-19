"""SQLite API tests for registered-user relationship invitations."""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import get_db
from app.main import app
from app.services import user_relationship_service


@pytest.mark.parametrize("accept_before_expiry", [False, True])
def test_expired_relationship_is_listed_as_expired_and_can_be_reinvited(
    client, monkeypatch, accept_before_expiry,
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
    invitation = client.post(url, headers=alice, json={**payload, "valid_until": expiry.isoformat()})
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


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE t_user (
                id INTEGER PRIMARY KEY, username VARCHAR(20) NOT NULL, display_name VARCHAR(100),
                avatar_url VARCHAR(500), phone VARCHAR(20) NOT NULL, sex SMALLINT DEFAULT 0,
                birth_date DATE, tags TEXT, user_role SMALLINT DEFAULT 0, created_by BIGINT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_by BIGINT,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_del BOOLEAN NOT NULL DEFAULT 0, onboarding_completed BOOLEAN NOT NULL DEFAULT 0
            )
        """))
        connection.execute(text("""
            CREATE TABLE t_auth_session (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id BIGINT NOT NULL,
                token_hash VARCHAR(64) NOT NULL UNIQUE, expires_at TIMESTAMP NOT NULL,
                created_by BIGINT, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_by BIGINT, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_del BOOLEAN NOT NULL DEFAULT 0
            )
        """))
        connection.execute(text("""
            CREATE TABLE t_user_relationship (
                id INTEGER PRIMARY KEY AUTOINCREMENT, relationship_pair_id CHAR(32) NOT NULL,
                subject_user_id BIGINT NOT NULL, related_user_id BIGINT NOT NULL,
                relationship_type VARCHAR(30) NOT NULL, relation_code VARCHAR(30) NOT NULL,
                relationship_label VARCHAR(100), status VARCHAR(20) NOT NULL,
                authorization_method VARCHAR(30) NOT NULL, authorization_reference VARCHAR(255),
                initiated_by_user_id BIGINT NOT NULL, verified_by_user_id BIGINT,
                valid_from TIMESTAMP, valid_until TIMESTAMP, accepted_at TIMESTAMP,
                revoked_at TIMESTAMP, revocation_reason VARCHAR(255), created_by BIGINT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_by BIGINT,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, is_del BOOLEAN NOT NULL DEFAULT 0,
                UNIQUE (relationship_pair_id, subject_user_id)
            )
        """))
        connection.execute(text("""
            INSERT INTO t_user (id, username, display_name, phone, created_at, updated_at, is_del)
            VALUES (1, 'alice', 'Alice', '13800000001', :now, :now, 0),
                   (2, 'bob', 'Bob', '13800000002', :now, :now, 0)
        """), {"now": now})

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


def test_relationship_list_paginates_groups_and_preserves_current_user_direction(client):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    url = "/api/users/me/relationships"
    for kind, code in (("family", "parent_of"), ("other", "other")):
        invitation = client.post(url, headers=alice, json={
            "related_user_id": 2, "relationship_type": kind, "relation_code": code,
        })
        assert invitation.status_code == 201, invitation.text
        if kind == "family":
            assert client.post(f"{url}/{invitation.json()['id']}/accept", headers=bob).status_code == 200

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
            session.execute(text("UPDATE t_user_relationship SET status = 'active' WHERE id = :id"),
                            {"id": invitation_id})
        else:
            session.execute(text("UPDATE t_user_relationship SET relation_code = 'sibling_of' WHERE id <> :id"),
                            {"id": invitation_id})

    for headers in (alice, bob):
        assert client.get(url, headers=headers).json()["items"][0]["status"] == "requires_confirmation"
    replacement = client.post(url, headers=alice, json=payload)
    assert replacement.status_code == 201, replacement.text
    assert client.post(f"{url}/{replacement.json()['id']}/accept", headers=bob).status_code == 200
    assert {item["status"] for item in client.get(url, headers=alice).json()["items"]} == {
        "active", "requires_confirmation",
    }


def test_family_invitation_acceptance_creates_reciprocal_rows_and_revoke_updates_pair(client: TestClient):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")

    created = client.post("/api/users/me/relationships", headers=alice, json={
        "related_user_id": 2,
        "relationship_type": "family",
        "relation_code": "parent_of",
        "relationship_label": "女儿",
    })
    assert created.status_code == 201, created.text
    invitation_id = created.json()["id"]
    assert created.json()["status"] == "pending"

    duplicate = client.post("/api/users/me/relationships", headers=bob, json={
        "related_user_id": 1, "relationship_type": "family", "relation_code": "child_of",
    })
    assert duplicate.status_code == 409

    accepted = client.post(f"/api/users/me/relationships/{invitation_id}/accept", headers=bob)
    assert accepted.status_code == 200, accepted.text
    pair = accepted.json()
    assert len(pair) == 2
    assert {(row["subject_user_id"], row["related_user_id"], row["relation_code"]) for row in pair} == {
        (1, 2, "parent_of"), (2, 1, "child_of")
    }
    assert len({row["relationship_pair_id"] for row in pair}) == 1

    bob_relationships = client.get("/api/users/me/relationships", headers=bob)
    assert len(bob_relationships.json()["items"]) == 1
    assert bob_relationships.json()["items"][0]["relation_code"] == "child_of"

    inverse_id = bob_relationships.json()["items"][0]["id"]
    revoked = client.post(f"/api/users/me/relationships/{inverse_id}/revoke", headers=bob)
    assert revoked.status_code == 200
    assert len(revoked.json()) == 2
    assert {row["status"] for row in revoked.json()} == {"revoked"}
    assert client.post(f"/api/users/me/relationships/{invitation_id}/revoke", headers=alice).status_code == 409


def test_agent_relationship_stays_directional_and_reject_is_recorded(client: TestClient):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    invite = client.post("/api/users/me/relationships", headers=alice, json={
        "related_user_id": 2,
        "relationship_type": "agent",
        "relation_code": "agent_for",
    })
    assert invite.status_code == 201, invite.text
    rejected = client.post(f"/api/users/me/relationships/{invite.json()['id']}/reject", headers=bob)
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_relationship_invitation_rejects_mismatched_code_and_unauthorized_accept(client: TestClient):
    alice = login(client, "13800000001")
    bob = login(client, "13800000002")
    invalid = client.post("/api/users/me/relationships", headers=alice, json={
        "related_user_id": 2,
        "relationship_type": "family",
        "relation_code": "agent_for",
    })
    assert invalid.status_code == 422

    invite = client.post("/api/users/me/relationships", headers=alice, json={
        "related_user_id": 2,
        "relationship_type": "family",
        "relation_code": "sibling_of",
    })
    assert invite.status_code == 201
    unauthorized = client.post(f"/api/users/me/relationships/{invite.json()['id']}/accept", headers=alice)
    assert unauthorized.status_code == 404
