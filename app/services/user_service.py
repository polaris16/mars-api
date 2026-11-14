"""用户查询服务。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TUser


class UserService:
    def list_all(self, session: Session) -> list[TUser]:
        """按主键排序，返回表中全部记录（包括软删除记录）。"""
        return list(session.scalars(select(TUser).order_by(TUser.id)).all())
