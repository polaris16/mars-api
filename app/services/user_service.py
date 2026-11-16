"""用户查询服务。"""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TUser
from app.schemas.user_profile import UserProfileUpdateRequest


class UserService:
    def list_all(self, session: Session) -> list[TUser]:
        """按主键排序，返回表中全部记录（包括软删除记录）。"""
        return list(session.scalars(select(TUser).order_by(TUser.id)).all())

    def get_profile(self, user: TUser) -> TUser:
        """返回已由鉴权依赖确认身份的当前用户。"""
        return user

    def update_profile(
        self,
        session: Session,
        user: TUser,
        payload: UserProfileUpdateRequest,
    ) -> TUser:
        """只更新 PATCH 请求实际提交的资料字段。"""
        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="至少提供一个待更新字段")

        with session.begin():
            current_user = session.scalar(
                select(TUser).where(TUser.id == user.id, TUser.is_del.is_(False))
            )
            if current_user is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")
            for field, value in updates.items():
                setattr(current_user, field, value)
            current_user.updated_by = current_user.id
            current_user.updated_at = datetime.now(timezone.utc)
        return current_user
