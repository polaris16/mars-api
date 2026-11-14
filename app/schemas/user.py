"""t_user 查询响应。"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str | None
    phone: str
    sex: int | None
    birth_date: date | None
    tags: list[str] | None
    user_role: int | None
    created_by: int | None
    created_at: datetime
    updated_by: int | None
    updated_at: datetime
    is_del: bool
