"""查询用户表的接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.user import UserResponse
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserResponse], summary="查询 t_user 全部记录")
def list_users(
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[UserService, Depends(UserService)],
) -> list[UserResponse]:
    return [UserResponse.model_validate(user) for user in service.list_all(session)]
