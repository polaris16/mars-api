"""验证配置、会话事务与无需数据库的应用启动。"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.db import database
from app.main import app


def test_settings_load_dotenv_and_environment_override(tmp_path, monkeypatch):
    for name in os.environ:
        if name.upper().startswith(("POSTGRES_", "DB_")):
            monkeypatch.delenv(name)

    env_file = tmp_path / ".env"
    password = "p@ss:/word#100%"
    env_file.write_text(
        "POSTGRES_HOST=dotenv-host\n"
        "POSTGRES_USER=test-user\n"
        f"POSTGRES_PASSWORD='{password}'\n"
        "POSTGRES_DATABASE=test-db\n"
        "DB_POOL_SIZE=7\n"
        "APP_NAME=ignored-extra-setting\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("POSTGRES_HOST", "environment-host")

    settings = Settings(_env_file=env_file)

    assert settings.postgres_host == "environment-host"
    assert settings.db_pool_size == 7
    assert settings.database_url.drivername == "postgresql+psycopg"
    assert settings.database_url.port == 5432
    assert settings.database_url.query == {}
    assert settings.database_url.username == "test-user"
    assert settings.database_url.database == "test-db"
    assert settings.database_url.password == password
    assert password not in repr(settings)
    assert password not in str(settings.database_url)


@pytest.fixture
def sqlite_engine(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE entries (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    try:
        yield engine
    finally:
        engine.dispose()


def test_service_transaction_commits(sqlite_engine):
    provider = database.get_db()
    session = next(provider)
    try:
        with session.begin():
            session.execute(text("INSERT INTO entries (id) VALUES (1)"))
    finally:
        provider.close()

    with sqlite_engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM entries")) == 1


def test_uncommitted_changes_rollback_when_session_closes(sqlite_engine):
    provider = database.get_db()
    session = next(provider)
    try:
        session.execute(text("INSERT INTO entries (id) VALUES (1)"))
    finally:
        provider.close()

    with sqlite_engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM entries")) == 0


def test_failed_service_transaction_rolls_back(sqlite_engine):
    provider = database.get_db()
    session = next(provider)
    try:
        with pytest.raises(RuntimeError, match="business failure"):
            with session.begin():
                session.execute(text("INSERT INTO entries (id) VALUES (1)"))
                raise RuntimeError("business failure")
    finally:
        provider.close()

    with sqlite_engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM entries")) == 0


def test_application_does_not_connect_to_postgres_on_startup(monkeypatch):
    def unexpected_connection(*args, **kwargs):
        pytest.fail("startup and health checks must not connect to PostgreSQL")

    monkeypatch.setattr(database.engine, "connect", unexpected_connection)
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/docs").status_code == 200
