"""手机号验证码认证、自动注册及 Token 会话服务。"""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuthSession, TUser


class AuthService:
    # 登录态最长保留 30 天；每次登录都会创建独立会话。
    token_ttl = timedelta(days=30)
    # 开发阶段固定验证码；接入短信服务后替换发送和校验实现。
    development_code = "123456"
    code_ttl_seconds = 300

    @staticmethod
    def _token_hash(token: str) -> str:
        """只保存 Token 摘要，避免数据库泄漏时直接暴露可用凭证。"""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    # 模拟发送验证码，不实际发送短信。
    def send_code(self, phone: str) -> int:
        """对新老手机号统一返回模拟发送成功，不泄露账号是否已注册。"""
        return self.code_ttl_seconds

    def login(self, session: Session, phone: str, code: str) -> tuple[TUser, str, datetime, bool]:
        # 当前开发方案通过固定验证码和手机号匹配用户。
        # 先校验验证码，再只查询未软删除用户，避免已删除账号获得登录态。
        if code != self.development_code:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="验证码错误")
        is_new_user = False
        try:
            # 用户首次注册与会话签发处在同一事务中，失败时一起回滚。
            with session.begin():
                user = session.scalar(
                    select(TUser).where(TUser.phone == phone, TUser.is_del.is_(False))
                )
                if user is None:
                    # 新手机号首次登录即注册；手机号同时作为临时用户名。
                    user = TUser(username=phone, phone=phone, sex=0, user_role=0, is_del=False)
                    session.add(user)
                    session.flush()
                    is_new_user = True

                token = secrets.token_urlsafe(32)
                expires_at = datetime.now(timezone.utc) + self.token_ttl
                session.add(AuthSession(
                    user_id=user.id,
                    token_hash=self._token_hash(token),
                    expires_at=expires_at,
                    created_by=user.id,
                    updated_by=user.id,
                ))
        except IntegrityError:
            # 并发首次登录时唯一手机号索引保证只创建一个用户；竞争失败方复用已创建账号。
            with session.begin():
                user = session.scalar(
                    select(TUser).where(TUser.phone == phone, TUser.is_del.is_(False))
                )
                if user is None:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="无法创建用户")
                is_new_user = False
                # 返回给客户端的随机原文只出现这一次；数据库仅持久化其 SHA-256 摘要。
                token = secrets.token_urlsafe(32)
                expires_at = datetime.now(timezone.utc) + self.token_ttl
                session.add(AuthSession(
                    user_id=user.id,
                    token_hash=self._token_hash(token),
                    expires_at=expires_at,
                    created_by=user.id,
                    updated_by=user.id,
                ))
        return user, token, expires_at, is_new_user

    def logout(self, session: Session, token: str) -> None:
        """按 Token 摘要定位并删除会话，使该登录凭证立即失效。"""
        token_hash = self._token_hash(token)
        with session.begin():
            auth_session = session.scalar(select(AuthSession).where(
                AuthSession.token_hash == token_hash,
                AuthSession.is_del.is_(False),
            ))
            # SQLite 测试库可能返回无时区时间；按 UTC 解释以保持比较一致。
            expires_at = auth_session.expires_at if auth_session is not None else None
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if auth_session is None or expires_at <= datetime.now(timezone.utc):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录凭证无效或已过期")
            # 按统一软删除约定撤销会话，后续鉴权会过滤 is_del=true。
            auth_session.is_del = True
            auth_session.updated_at = datetime.now(timezone.utc)
