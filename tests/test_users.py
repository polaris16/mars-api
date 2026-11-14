"""验证 t_user 全量查询接口和响应字段。"""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.db.database import get_db
from app.main import app
from app.models import TUser


def test_list_users_returns_all_rows_including_soft_deleted():
    now = datetime.now(timezone.utc)
    user = TUser(
        id=1,
        username="alice",
        display_name="Alice",
        phone="123",
        sex=1,
        birth_date=date(2000, 1, 2),
        tags=["test"],
        user_role=0,
        created_by=None,
        created_at=now,
        updated_by=None,
        updated_at=now,
        is_del=True,
    )
    session = MagicMock()
    session.scalars.return_value.all.return_value = [user]

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.get("/api/users")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "username": "alice",
            "display_name": "Alice",
            "phone": "123",
            "sex": 1,
            "birth_date": "2000-01-02",
            "tags": ["test"],
            "user_role": 0,
            "created_by": None,
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "updated_by": None,
            "updated_at": now.isoformat().replace("+00:00", "Z"),
            "is_del": True,
        }
    ]
    statement = session.scalars.call_args.args[0]
    assert statement.whereclause is None
    assert [str(column) for column in statement._order_by_clauses] == ["t_user.id"]
