"""API 通用依赖，例如从 Bearer Token 解析当前用户。"""

import hashlib
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AfterValidator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models import AuthSession, User

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_db)],
) -> User:
    """验证 Token 会话并返回对应的有效用户。"""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

    token_hash = hashlib.sha256(credentials.credentials.encode("utf-8")).hexdigest()
    auth_session = session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == token_hash,
            AuthSession.is_del.is_(False),
        )
    )
    if auth_session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录凭证无效或已过期")

    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录凭证无效或已过期")

    user = session.scalar(
        select(User).where(User.id == auth_session.user_id, User.is_del.is_(False))
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")
    # 回滚结束鉴权阶段的只读事务，让后续 Service 可以显式开启自己的写事务。
    session.rollback()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


# 让仅支持分页的列表在参数校验阶段给出明确的 422。
def _require_pagination(value: bool) -> bool:
    if not value:
        raise ValueError("此列表仅支持分页，请省略 paginate 或设为 true")
    return value


Paginate = Annotated[bool, Query(description="设为 false 时返回全部匹配记录")]
PaginateOnly = Annotated[
    bool,
    Query(description="此列表仅支持分页，设为 false 时返回 422"),
    AfterValidator(_require_pagination),
]
