"""当前用户个人资料接口的请求与响应模型。"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    avatar_url: str | None
    display_name: str | None
    sex: int | None
    birth_date: date | None


class UserProfileUpdateRequest(BaseModel):
    # PATCH 支持只提交需要修改的字段；显式传 null 可清空可选字段。
    avatar_url: str | None = Field(default=None, max_length=500)
    display_name: str | None = Field(default=None, max_length=100)
    sex: int | None = Field(default=None, ge=0, le=2)
    birth_date: date | None = None
