"""认证接口请求与响应模型。"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.user import UserResponse


class LoginRequest(BaseModel):
    # [旧字段，保留] 原登录方案要求用户名与手机号同时提交。
    # username: str = Field(min_length=1, max_length=20)
    phone: str = Field(min_length=1, max_length=20)
    # [新增] 开发阶段使用固定验证码，正式接短信后沿用此字段。
    code: str = Field(min_length=6, max_length=6)


# [新增] 模拟发送验证码请求。
class SendCodeRequest(BaseModel):
    phone: str = Field(min_length=1, max_length=20)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserResponse


class MessageResponse(BaseModel):
    message: str


# [新增] 模拟发送验证码响应。
class SendCodeResponse(BaseModel):
    message: str
    expires_in: int
