"""通过独立 SQLite 数据库测试登录、模拟验证码与登出接口。"""

from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import get_db
from app.main import app
from app.services.auth_service import AuthService


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # t_user 使用 PostgreSQL ARRAY 和序列，测试库用 SQLite 等价字段建临时表。
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE t_user (
                id INTEGER PRIMARY KEY,
                username VARCHAR(20) NOT NULL,
                display_name VARCHAR(100),
                avatar_url VARCHAR(500),
                phone VARCHAR(20) NOT NULL,
                sex SMALLINT DEFAULT 0,
                birth_date DATE,
                tags TEXT,
                user_role SMALLINT DEFAULT 0,
                created_by BIGINT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_by BIGINT,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_del BOOLEAN NOT NULL DEFAULT 0,
                onboarding_completed BOOLEAN NOT NULL DEFAULT 0
            )
        """))
        connection.execute(text("""
            CREATE TABLE t_auth_session (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id BIGINT NOT NULL REFERENCES t_user(id) ON DELETE CASCADE,
                token_hash VARCHAR(64) NOT NULL UNIQUE,
                expires_at TIMESTAMP NOT NULL,
                created_by BIGINT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_by BIGINT,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_del BOOLEAN NOT NULL DEFAULT 0
            )
        """))
        connection.execute(text("CREATE UNIQUE INDEX uk_user_phone ON t_user(phone) WHERE is_del = 0"))
        connection.execute(text("CREATE UNIQUE INDEX uk_user_username ON t_user(username) WHERE is_del = 0"))
        connection.execute(text("""
            INSERT INTO t_user (id, username, display_name, phone, created_at, updated_at, is_del)
            VALUES (1, 'alice', 'Alice', '13800000000', :now, :now, 0)
        """), {"now": datetime.now(timezone.utc).isoformat()})
        connection.execute(text("""
            INSERT INTO t_user (id, username, display_name, phone, created_at, updated_at, is_del)
            VALUES (2, 'deleted', 'Deleted', '13900000000', :now, :now, 1)
        """), {"now": datetime.now(timezone.utc).isoformat()})

    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_send_code_returns_mock_success_without_sending_sms(client: TestClient):
    response = client.post("/api/auth/send-code", json={"phone": "13800000000"})

    assert response.status_code == 200
    assert response.json() == {"message": "验证码已发送", "expires_in": AuthService.code_ttl_seconds}


def test_send_code_returns_same_success_for_unknown_or_deleted_phone(client: TestClient):
    for phone in ("13700000000", "13900000000"):
        response = client.post("/api/auth/send-code", json={"phone": phone})
        assert response.status_code == 200


def test_login_with_fixed_code_returns_bearer_token(client: TestClient):
    response = client.post(
        "/api/auth/login",
        json={"phone": "13800000000", "code": "123456"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["id"] == 1
    assert set(body["user"]) == {"id", "display_name", "avatar_url"}
    assert body["is_new_user"] is False
    assert body["onboarding_completed"] is False
    assert datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)


@pytest.mark.parametrize(
    "phone",
    ["13700000000", "13900000000"],
)
def test_login_registers_unknown_or_previously_deleted_phone(client: TestClient, phone: str):
    response = client.post("/api/auth/login", json={"phone": phone, "code": "123456"})

    assert response.status_code == 200
    assert response.json()["is_new_user"] is True
    assert response.json()["onboarding_completed"] is False
    assert response.json()["user"]["id"] != 2


def test_login_rejects_invalid_code(client: TestClient):
    response = client.post("/api/auth/login", json={"phone": "13800000000", "code": "000000"})

    assert response.status_code == 401


def test_logout_revokes_token_and_second_logout_fails(client: TestClient):
    login_response = client.post(
        "/api/auth/login",
        json={"phone": "13800000000", "code": "123456"},
    )
    token = login_response.json()["access_token"]

    logout_response = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    second_logout_response = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})

    assert logout_response.status_code == 200
    assert logout_response.json() == {"message": "已退出登录"}
    assert second_logout_response.status_code == 401


def test_logout_requires_bearer_token(client: TestClient):
    response = client.post("/api/auth/logout")

    assert response.status_code == 401


def test_get_my_profile_requires_token_and_returns_profile_fields(client: TestClient):
    anonymous_response = client.get("/api/users/me/profile")
    login_response = client.post(
        "/api/auth/login",
        json={"phone": "13800000000", "code": "123456"},
    )
    token = login_response.json()["access_token"]

    response = client.get("/api/users/me/profile", headers={"Authorization": f"Bearer {token}"})

    assert anonymous_response.status_code == 401
    assert response.status_code == 200
    assert response.json() == {
        "avatar_url": None,
        "display_name": "Alice",
        "sex": 0,
        "birth_date": None,
    }


def test_patch_my_profile_updates_and_persists_profile(client: TestClient):
    login_response = client.post(
        "/api/auth/login",
        json={"phone": "13800000000", "code": "123456"},
    )
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.patch(
        "/api/users/me/profile",
        headers=headers,
        json={
            "display_name": "新昵称",
            "sex": 2,
            "birth_date": "1950-06-15",
            "avatar_url": "https://cdn.example/avatar.jpg",
        },
    )
    subsequent_get = client.get("/api/users/me/profile", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "avatar_url": "https://cdn.example/avatar.jpg",
        "display_name": "新昵称",
        "sex": 2,
        "birth_date": "1950-06-15",
    }
    assert subsequent_get.json() == response.json()


def test_patch_my_profile_rejects_invalid_sex_and_empty_body(client: TestClient):
    login_response = client.post(
        "/api/auth/login",
        json={"phone": "13800000000", "code": "123456"},
    )
    headers = {"Authorization": f"Bearer {login_response.json()['access_token']}"}

    invalid_sex = client.patch("/api/users/me/profile", headers=headers, json={"sex": 3})
    empty_update = client.patch("/api/users/me/profile", headers=headers, json={})

    assert invalid_sex.status_code == 422
    assert empty_update.status_code == 422


def test_first_login_registers_user_and_later_login_reports_onboarding_state(client: TestClient):
    first_login = client.post("/api/auth/login", json={"phone": "13700000000", "code": "123456"})
    second_login = client.post("/api/auth/login", json={"phone": "13700000000", "code": "123456"})

    assert first_login.status_code == 200
    assert first_login.json()["is_new_user"] is True
    assert first_login.json()["onboarding_completed"] is False
    assert set(first_login.json()["user"]) == {"id", "display_name", "avatar_url"}
    assert second_login.status_code == 200
    assert second_login.json()["is_new_user"] is False
    assert second_login.json()["onboarding_completed"] is False
