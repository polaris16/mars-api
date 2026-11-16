"""查询用户表的接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.api.dependencies import CurrentUser
from app.schemas.interest import InterestResponse, UserInterestsResponse, UserInterestsUpdateRequest
from app.schemas.user_profile import UserProfileResponse, UserProfileUpdateRequest
from app.schemas.user import UserResponse
from app.services.user_service import UserService
from app.services.interest_service import InterestService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserResponse], summary="查询 t_user 全部记录")
def list_users(
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[UserService, Depends(UserService)],
) -> list[UserResponse]:
    # TODO(security): 上线前限制为管理员访问，排除软删除用户并收窄返回字段，避免暴露手机号等个人信息。
    return [UserResponse.model_validate(user) for user in service.list_all(session)]


# 获取当前登录用户的头像、昵称、性别和生日。
@router.get("/me/profile", response_model=UserProfileResponse, summary="获取我的个人资料")
def get_my_profile(
    current_user: CurrentUser,
    service: Annotated[UserService, Depends(UserService)],
) -> UserProfileResponse:
    user = service.get_profile(current_user)
    return UserProfileResponse.model_validate(user)


# 局部更新当前登录用户的个人资料。
@router.patch("/me/profile", response_model=UserProfileResponse, summary="更新我的个人资料")
def update_my_profile(
    payload: UserProfileUpdateRequest,
    current_user: CurrentUser,
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[UserService, Depends(UserService)],
) -> UserProfileResponse:
    user = service.update_profile(session, current_user, payload)
    return UserProfileResponse.model_validate(user)


# 读取当前用户已选兴趣领域。
@router.get("/me/interests", response_model=UserInterestsResponse, summary="获取我的兴趣领域")
def get_my_interests(
    current_user: CurrentUser,
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[InterestService, Depends(InterestService)],
) -> UserInterestsResponse:
    interests = service.get_user_interests(session, current_user)
    return UserInterestsResponse(
        interest_ids=[item.id for item in interests],
        interests=[service.to_response(item) for item in interests],
    )


# 保存当前用户的兴趣领域 ID 列表，不限制选择数量。
@router.put("/me/interests", response_model=UserInterestsResponse, summary="保存我的兴趣领域")
def save_my_interests(
    payload: UserInterestsUpdateRequest,
    current_user: CurrentUser,
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[InterestService, Depends(InterestService)],
) -> UserInterestsResponse:
    interests = service.save_user_interests(session, current_user, payload.interest_ids)
    return UserInterestsResponse(
        interest_ids=[item.id for item in interests],
        interests=[service.to_response(item) for item in interests],
    )
