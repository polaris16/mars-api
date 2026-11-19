"""认证接口请求与响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginUserResponse(BaseModel):
    """登录成功后返回给客户端的最小用户展示信息。"""

    # 允许从 SQLAlchemy ORM 用户对象读取响应字段。
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str | None
    avatar_url: str | None


class LoginRequest(BaseModel):
    phone: str = Field(min_length=1, max_length=20)
    # 开发阶段使用固定验证码，正式接短信后沿用此字段。
    code: str = Field(min_length=6, max_length=6)


# 模拟发送验证码请求。
class SendCodeRequest(BaseModel):
    phone: str = Field(min_length=1, max_length=20)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    is_new_user: bool
    onboarding_completed: bool
    user: LoginUserResponse


class MessageResponse(BaseModel):
    message: str


# 模拟发送验证码响应。
class SendCodeResponse(BaseModel):
    message: str
    expires_in: int
