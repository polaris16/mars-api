"""手机号与用户名认证及 Token 会话服务。"""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from fastapi import HTTPException, status
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
    def send_code(self, session: Session, phone: str) -> int:
        """仅允许有效用户请求模拟验证码；当前固定码不会实际发送或存储。"""
        user = session.scalar(select(TUser.id).where(TUser.phone == phone, TUser.is_del.is_(False)))
        if user is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法发送验证码")
        return self.code_ttl_seconds

    def login(self, session: Session, phone: str, code: str) -> tuple[TUser, str, datetime]:
        # 当前开发方案通过固定验证码和手机号匹配用户。
        # 先校验验证码，再只查询未软删除用户，避免已删除账号获得登录态。
        if code != self.development_code:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="验证码错误")
        user = session.scalar(
            select(TUser).where(TUser.phone == phone, TUser.is_del.is_(False))
        )
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="手机号或验证码不正确")

        # 返回给客户端的随机原文只出现这一次；数据库仅持久化其 SHA-256 摘要。
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + self.token_ttl
        # 查询用户已开启 Session 事务，先提交查询事务，再用独立事务保存会话。
        session.commit()
        with session.begin():
            session.add(AuthSession(user_id=user.id, token_hash=self._token_hash(token), expires_at=expires_at))
        return user, token, expires_at

    def logout(self, session: Session, token: str) -> None:
        """按 Token 摘要定位并删除会话，使该登录凭证立即失效。"""
        token_hash = self._token_hash(token)
        auth_session = session.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash))
        # SQLite 测试库可能返回无时区时间；按 UTC 解释以保持比较一致。
        expires_at = auth_session.expires_at if auth_session is not None else None
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if auth_session is None or expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录凭证无效或已过期")
        session.commit()
        with session.begin():
            session.delete(auth_session)
