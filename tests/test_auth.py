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
                phone VARCHAR(20) NOT NULL,
                sex SMALLINT DEFAULT 0,
                birth_date DATE,
                tags TEXT,
                user_role SMALLINT DEFAULT 0,
                created_by BIGINT,
                created_at TIMESTAMP NOT NULL,
                updated_by BIGINT,
                updated_at TIMESTAMP NOT NULL,
                is_del BOOLEAN NOT NULL DEFAULT 0
            )
        """))
        connection.execute(text("""
            CREATE TABLE auth_session (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id BIGINT NOT NULL REFERENCES t_user(id) ON DELETE CASCADE,
                token_hash VARCHAR(64) NOT NULL UNIQUE,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
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


def test_send_code_rejects_unknown_or_deleted_phone(client: TestClient):
    for phone in ("13700000000", "13900000000"):
        response = client.post("/api/auth/send-code", json={"phone": phone})
        assert response.status_code == 400


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
    assert body["user"]["phone"] == "13800000000"
    assert datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)


@pytest.mark.parametrize(
    ("phone", "code"),
    [
        ("13800000000", "000000"),
        ("13700000000", "123456"),
        ("13900000000", "123456"),
    ],
)
def test_login_rejects_invalid_code_unknown_or_deleted_user(client: TestClient, phone: str, code: str):
    response = client.post("/api/auth/login", json={"phone": phone, "code": code})

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
