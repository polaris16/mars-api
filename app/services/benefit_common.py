"""[新增] 权益模块的权限、分页和凭据保护。"""

import base64
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import BenefitStaff, Enterprise, EnterpriseMember, User


class BenefitError(HTTPException):
    def __init__(self, status: int, code: str, detail: str, *, extra: dict | None = None):
        super().__init__(status_code=status, detail=detail)
        self.code = code
        # 部分业务拒绝同时返回可直接用于界面更新的计数。
        self.extra = extra or {}


def conflict(code: str, detail: str) -> BenefitError:
    return BenefitError(409, code, detail)


def missing() -> BenefitError:
    return BenefitError(404, "benefit_not_found", "权益资源不存在")


def forbidden() -> BenefitError:
    return BenefitError(403, "benefit_forbidden", "没有此项权益操作权限")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc(value: datetime) -> datetime:
    # SQLite 测试库可能去掉时区。PostgreSQL 返回有时区值。
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_phone(value: str) -> str:
    value = value.strip().removeprefix("+")
    if len(value) == 13 and value.startswith("86"):
        return value[2:]
    return value


def display(row, fields: str) -> dict:
    return {key: getattr(row, key) for key in fields.split()}


def page_data(items: list, page: int, size: int, total: int) -> dict:
    return {"items": items, "page": page, "page_size": size, "total": total}


# 目录列表可先选出完整结果或当前页，再批量组装子项。
def select_page(db: Session, statement, page: int, size: int, *, paginate_results: bool):
    if paginate_results:
        total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
        rows = db.scalars(statement.offset((page - 1) * size).limit(size)).all()
        return rows, page, size, total
    rows = db.scalars(statement).all()
    return rows, 1, len(rows), len(rows)


def paginate(
    db: Session, statement, page: int, size: int, render, *, paginate_results=True
) -> dict:
    rows, page, size, total = select_page(
        db, statement, page, size, paginate_results=paginate_results
    )
    return page_data([render(row) for row in rows], page, size, total)


def active_row(db: Session, model, row_id: int, *, lock: bool = False):
    query = select(model).where(model.id == row_id, model.is_del.is_(False))
    row = db.scalar(
        query.with_for_update().execution_options(populate_existing=True) if lock else query
    )
    if row is None:
        raise missing()
    return row


def flush_unique(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        # 唯一冲突的并发请求返回业务错误，其余约束异常保留给服务端诊断。
        origin = exc.orig
        if getattr(origin, "sqlstate", None) == "23505" or getattr(
            origin, "sqlite_errorname", None
        ) in {"SQLITE_CONSTRAINT_UNIQUE", "SQLITE_CONSTRAINT_PRIMARYKEY"}:
            raise conflict("benefit_duplicate", "该业务标识已经存在") from exc
        raise


def stamp(row, actor_id: int) -> None:
    row.updated_by = actor_id
    row.updated_at = now_utc()


def new_row(model, actor_id: int, **kwargs):
    return model(**kwargs, created_by=actor_id, updated_by=actor_id)


class BenefitContext:
    def __init__(self, db: Session, actor_id: int | None):
        self.db = db
        self.actor_id = actor_id

    def platform_roles(self) -> set[str]:
        roles = set(
            self.db.scalars(
                select(BenefitStaff.role).where(
                    BenefitStaff.user_id == self.actor_id,
                    BenefitStaff.status == "active",
                    BenefitStaff.is_del.is_(False),
                )
            )
        )
        if self.actor_id in get_settings().benefit_bootstrap_admin_user_ids:
            roles.add("admin")
        return roles

    def require_platform(self, *roles: str) -> None:
        if not self.platform_roles().intersection(roles):
            raise forbidden()

    def enterprise(self, enterprise_id: int, *roles: str, lock: bool = False):
        enterprise = active_row(self.db, Enterprise, enterprise_id, lock=lock)
        member = self.db.scalar(
            select(EnterpriseMember).where(
                EnterpriseMember.enterprise_id == enterprise_id,
                EnterpriseMember.user_id == self.actor_id,
                EnterpriseMember.is_del.is_(False),
                EnterpriseMember.status == "active",
            )
        )
        if member is None or enterprise.status != "active":
            raise missing()
        if roles and member.role not in roles:
            raise forbidden()
        return enterprise, member

    def phone(self) -> str:
        user = active_row(self.db, User, self.actor_id)
        phone = normalize_phone(user.phone)
        if not re.fullmatch(r"[0-9]{7,15}", phone):
            raise BenefitError(403, "benefit_phone_unavailable", "请使用有效手机号登录")
        return phone


class BenefitKeys:
    """版本化密钥分别用于鉴别摘要和加密可重放的交付副本。"""

    def __init__(self):
        value = get_settings().benefit_crypto_keys.get_secret_value()
        try:
            pairs = [part.split(":", 1) for part in value.split(",") if part.strip()]
            self.keys = {
                int(version): base64.b64decode(encoded, altchars=b"-_", validate=True)
                for version, encoded in pairs
            }
            if (
                not self.keys
                or len(self.keys) != len(pairs)
                or any(
                    not 1 <= version <= 32767 or len(secret) != 32
                    for version, secret in self.keys.items()
                )
            ):
                raise ValueError("invalid benefit key ring")
        except ValueError:
            raise BenefitError(503, "benefit_key_unavailable", "权益密钥配置不可用") from None
        self.current = max(self.keys)

    def digest(self, version: int, purpose: str, content: str) -> bytes:
        secret = self.keys.get(version)
        if secret is None:
            raise BenefitError(503, "benefit_key_unavailable", "权益历史密钥不可用")
        return hmac.digest(
            secret, f"mars-benefit:{purpose}:".encode() + content.encode(), hashlib.sha256
        )

    def fingerprint(self, version: int, purpose: str, values: dict) -> bytes:
        data = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return self.digest(version, purpose, data)

    def seal(self, version: int, purpose: str, value: str) -> bytes:
        key = self.digest(version, "encryption", purpose)
        return Fernet(base64.urlsafe_b64encode(key)).encrypt(value.encode())

    def open(self, version: int, purpose: str, token: bytes) -> str:
        key = self.digest(version, "encryption", purpose)
        try:
            return Fernet(base64.urlsafe_b64encode(key)).decrypt(token).decode()
        except (InvalidToken, UnicodeDecodeError):
            raise BenefitError(
                503, "benefit_ciphertext_unavailable", "权益加密资料无法读取"
            ) from None
