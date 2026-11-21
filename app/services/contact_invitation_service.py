"""[新增] 签发与验证由用户主动分享的好友邀请码。"""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuthSession, User
from app.schemas.user_relationship import ContactCodeResponse

CODE_TTL = timedelta(hours=24)
SIGNED_CONTEXT = "mars:contact-invitation:v1"


class ContactInvitationService:
    @staticmethod
    def _signature(session: AuthSession, expires: int) -> str:
        # 用独立的用途签名，分享码不含登录 Token 或其摘要。
        payload = f"{SIGNED_CONTEXT}:{session.id}:{expires}".encode("ascii")
        return hmac.new(bytes.fromhex(session.token_hash), payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _active_sessions(now: datetime):
        return (
            select(AuthSession)
            .join(User, User.id == AuthSession.user_id)
            .where(
                AuthSession.is_del.is_(False), User.is_del.is_(False), AuthSession.expires_at > now
            )
        )

    def issue(self, db: Session, user_id: int, bearer_token: str) -> ContactCodeResponse:
        now = datetime.now(timezone.utc)
        login = db.scalar(
            self._active_sessions(now).where(
                AuthSession.user_id == user_id,
                AuthSession.token_hash == hashlib.sha256(bearer_token.encode("utf-8")).hexdigest(),
            )
        )
        if login is None:
            raise HTTPException(401, "登录凭证无效或已过期")
        end = login.expires_at
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        expires = int(min(end, now + CODE_TTL).timestamp())
        if expires <= now.timestamp():
            raise HTTPException(401, "登录凭证即将过期，请重新登录")
        code = f"{login.id}.{expires}.{self._signature(login, expires)}"
        return ContactCodeResponse(
            code=code, expires_at=datetime.fromtimestamp(expires, timezone.utc)
        )

    def target_user_id(self, db: Session, code: str) -> int:
        session_id, expiry, signature = code.split(".")
        now = datetime.now(timezone.utc)
        if int(session_id) > 2**63 - 1 or int(expiry) <= now.timestamp():
            raise HTTPException(404, "邀请码无效或已过期")
        login = db.scalar(self._active_sessions(now).where(AuthSession.id == int(session_id)))
        if login is None or not hmac.compare_digest(signature, self._signature(login, int(expiry))):
            raise HTTPException(404, "邀请码无效或已过期")
        return login.user_id
