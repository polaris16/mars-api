"""通过 SQLite 测试兴趣领域目录及用户兴趣选择接口。"""

from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import get_db
from app.main import app


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
            CREATE TABLE t_interest_domain (
                id INTEGER PRIMARY KEY, code VARCHAR(50) NOT NULL UNIQUE, name VARCHAR(50) NOT NULL UNIQUE,
                is_enabled BOOLEAN NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0,
                created_by BIGINT, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_by BIGINT, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_del BOOLEAN NOT NULL DEFAULT 0
            )
        """))
        connection.execute(text("""
            CREATE TABLE t_user_interest (
                user_id BIGINT NOT NULL, interest_id BIGINT NOT NULL,
                created_by BIGINT, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_by BIGINT, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_del BOOLEAN NOT NULL DEFAULT 0, PRIMARY KEY (user_id, interest_id)
            )
        """))
        connection.execute(text("""
            INSERT INTO t_user (id, username, display_name, phone, created_at, updated_at, is_del)
            VALUES (1, 'alice', 'Alice', '13800000000', :now, :now, 0)
        """), {"now": now})
        connection.execute(text("""
            INSERT INTO t_interest_domain (id, code, name, is_enabled, sort_order, is_del)
            VALUES (1, 'reading', '阅读', 1, 20, 0),
                   (2, 'music', '音乐', 1, 10, 0),
                   (3, 'disabled', '停用项', 0, 30, 0),
                   (4, 'deleted', '已删除项', 1, 40, 1)
        """))

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


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"phone": "13800000000", "code": "123456"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_list_available_interests_returns_enabled_and_ordered(client: TestClient):
    response = client.get("/api/interests")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [2, 1]
    assert [item["name"] for item in response.json()] == ["音乐", "阅读"]


def test_get_my_interests_requires_login_and_returns_selection(client: TestClient, auth_headers: dict[str, str]):
    anonymous = client.get("/api/users/me/interests")
    client.put("/api/users/me/interests", headers=auth_headers, json={"interest_ids": [1, 2]})

    response = client.get("/api/users/me/interests", headers=auth_headers)

    assert anonymous.status_code == 401
    assert response.status_code == 200
    assert response.json()["interest_ids"] == [2, 1]


def test_save_interests_replaces_selection_and_restores_soft_deleted_rows(
    client: TestClient,
    auth_headers: dict[str, str],
):
    first = client.put("/api/users/me/interests", headers=auth_headers, json={"interest_ids": [1, 2]})
    replacement = client.put("/api/users/me/interests", headers=auth_headers, json={"interest_ids": [1]})
    restored = client.put("/api/users/me/interests", headers=auth_headers, json={"interest_ids": [1, 2]})

    assert first.status_code == replacement.status_code == restored.status_code == 200
    assert replacement.json()["interest_ids"] == [1]
    assert restored.json()["interest_ids"] == [2, 1]


def test_interest_replacement_locks_user_before_reading_old_selection(client, auth_headers):
    operations = []

    def record_statement(state):
        if state.is_select:
            names = {getattr(source, "name", None) for source in state.statement.get_final_froms()}
            sql = str(state.statement.compile(dialect=postgresql.dialect()))
            operations.append((names, "FOR UPDATE" in sql))

    event.listen(Session, "do_orm_execute", record_statement)
    try:
        result = client.put("/api/users/me/interests", headers=auth_headers, json={"interest_ids": [1]})
    finally:
        event.remove(Session, "do_orm_execute", record_statement)
    assert result.status_code == 200, result.text
    assert ({"t_user"}, True) in operations
    assert operations.index(({"t_user"}, True)) < operations.index(({"t_user_interest"}, False))


def test_saving_interests_completes_onboarding_when_profile_is_complete(
    client: TestClient,
    auth_headers: dict[str, str],
):
    client.patch(
        "/api/users/me/profile",
        headers=auth_headers,
        json={"display_name": "Alice", "sex": 0, "birth_date": "1950-06-15"},
    )
    saved_interests = client.put(
        "/api/users/me/interests",
        headers=auth_headers,
        json={"interest_ids": [1]},
    )
    subsequent_login = client.post("/api/auth/login", json={"phone": "13800000000", "code": "123456"})

    assert saved_interests.status_code == 200
    assert subsequent_login.json()["onboarding_completed"] is True


@pytest.mark.parametrize("interest_ids", [[3], [4], [999], [1, 1]])
def test_save_interests_rejects_disabled_deleted_unknown_or_duplicate_ids(
    client: TestClient,
    auth_headers: dict[str, str],
    interest_ids: list[int],
):
    response = client.put(
        "/api/users/me/interests",
        headers=auth_headers,
        json={"interest_ids": interest_ids},
    )

    assert response.status_code == 422
