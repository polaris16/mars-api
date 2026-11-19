"""登录与登出接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LoginUserResponse,
    MessageResponse,
    SendCodeRequest,
    SendCodeResponse,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])
# logout 通过标准 Authorization: Bearer <token> 请求头接收客户端凭证。
bearer = HTTPBearer()


@router.post("/login", response_model=LoginResponse, summary="手机号验证码登录")
def login(
    payload: LoginRequest,
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[AuthService, Depends(AuthService)],
) -> LoginResponse:
    # 手机号 + 验证码登录。
    user, token, expires_at, is_new_user = service.login(session, payload.phone, payload.code)
    # 将 ORM 用户转换为公开响应模型，避免直接序列化数据库对象。
    return LoginResponse(
        access_token=token,
        expires_at=expires_at,
        is_new_user=is_new_user,
        onboarding_completed=user.onboarding_completed,
        user=LoginUserResponse.model_validate(user),
    )


# 开发阶段模拟发送验证码，不实际发送短信。
@router.post("/send-code", response_model=SendCodeResponse, summary="模拟发送短信验证码")
def send_code(
    payload: SendCodeRequest,
    service: Annotated[AuthService, Depends(AuthService)],
) -> SendCodeResponse:
    # 返回模拟成功状态和有效期提示，固定验证码由开发/联调双方约定。
    expires_in = service.send_code(payload.phone)
    return SendCodeResponse(message="验证码已发送", expires_in=expires_in)


@router.post("/logout", response_model=MessageResponse, summary="退出当前登录")
def logout(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[AuthService, Depends(AuthService)],
) -> MessageResponse:
    service.logout(session, credentials.credentials)
    return MessageResponse(message="已退出登录")
