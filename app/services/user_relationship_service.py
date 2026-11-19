"""Relationship invitation lifecycle and reciprocal family records."""

from datetime import datetime, timezone
import uuid
from collections import defaultdict

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import User, UserRelationship
from app.schemas.user_relationship import RelationshipCreateRequest, RelationshipListResponse, RelationshipRecordResponse


RECIPROCAL_CODES = {
    "parent_of": "child_of",
    "child_of": "parent_of",
    "spouse_of": "spouse_of",
    "sibling_of": "sibling_of",
    "related_to": "related_to",
}
ALLOWED_CODES = {
    "family": set(RECIPROCAL_CODES),
    "agent": {"agent_for"},
    "caregiver": {"caregiver_for"},
    "other": {"other"},
}


class UserRelationshipService:
    @staticmethod
    def _not_found() -> HTTPException:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关系不存在")

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @classmethod
    def _effective_status(cls, row: UserRelationship, now: datetime) -> str:
        if row.status in {"pending", "active"} and row.valid_until is not None:
            if cls._utc(row.valid_until) <= now:
                return "expired"
        return row.status

    @classmethod
    def is_confirmed_family_pair(cls, rows: list[UserRelationship], now: datetime) -> bool:
        if len(rows) != 2:
            return False
        first, second = rows
        return (
            all(row.relationship_type == "family" and cls._effective_status(row, now) == "active" for row in rows)
            and first.subject_user_id == second.related_user_id
            and first.related_user_id == second.subject_user_id
            and RECIPROCAL_CODES.get(first.relation_code) == second.relation_code
        )

    def list_for_user(
        self, session: Session, user: User, page: int, page_size: int,
    ) -> RelationshipListResponse:
        user_id = user.id
        visible_groups = select(UserRelationship.relationship_pair_id).where(
            or_(UserRelationship.subject_user_id == user_id, UserRelationship.related_user_id == user_id),
            UserRelationship.is_del.is_(False),
        )
        # Page whole groups so a reciprocal pair cannot be split or counted twice.
        group_order = (
            select(
                UserRelationship.relationship_pair_id,
                func.max(UserRelationship.created_at).label("latest_created_at"),
                func.max(UserRelationship.id).label("latest_id"),
            )
            .where(
                UserRelationship.relationship_pair_id.in_(visible_groups),
                UserRelationship.is_del.is_(False),
            )
            .group_by(UserRelationship.relationship_pair_id)
            .subquery()
        )
        total = session.scalar(select(func.count()).select_from(group_order)) or 0
        page_groups = (
            select(group_order)
            .order_by(group_order.c.latest_created_at.desc(), group_order.c.latest_id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .subquery()
        )
        rows = list(session.scalars(
            select(UserRelationship)
            .join(page_groups, page_groups.c.relationship_pair_id == UserRelationship.relationship_pair_id)
            .where(
                UserRelationship.is_del.is_(False),
            )
            .order_by(page_groups.c.latest_created_at.desc(), page_groups.c.latest_id.desc(), UserRelationship.id.desc())
        ).all())
        # For a confirmed family pair return this user's own directional row once;
        # for a one-way agent relation or incoming invitation return its sole row.
        groups: dict[uuid.UUID, list[UserRelationship]] = defaultdict(list)
        for row in rows:
            groups[row.relationship_pair_id].append(row)
        now = datetime.now(timezone.utc)
        result = []
        for group in groups.values():
            visible = [item for item in group if user_id in (item.subject_user_id, item.related_user_id)]
            row = next((item for item in visible if item.subject_user_id == user_id), visible[0])
            status = self._effective_status(row, now)
            if row.relationship_type == "family" and status == "active":
                if not self.is_confirmed_family_pair(group, now):
                    status = "requires_confirmation"
            result.append(RelationshipRecordResponse.model_validate(row).model_copy(update={"status": status}))
        return RelationshipListResponse(items=result, page=page, page_size=page_size, total=total)

    def create(self, session: Session, user: User, body: RelationshipCreateRequest) -> UserRelationship:
        user_id = user.id
        if body.related_user_id == user_id:
            raise HTTPException(status_code=422, detail="不能与自己建立关系")
        if body.relation_code not in ALLOWED_CODES[body.relationship_type]:
            raise HTTPException(status_code=422, detail="关系类别与关系方向不匹配")
        expiry = self._utc(body.valid_until) if body.valid_until is not None else None

        # [UPDATED] End the read transaction left by authentication before opening a write transaction.
        session.rollback()
        with session.begin():
            # Lock both users in ID order before checking for a duplicate in either direction.
            locked_ids = set(session.scalars(
                select(User.id)
                .where(User.id.in_((user_id, body.related_user_id)), User.is_del.is_(False))
                .order_by(User.id)
                .with_for_update()
            ).all())
            if user_id not in locked_ids:
                raise HTTPException(status_code=401, detail="用户不存在或已停用")
            if body.related_user_id not in locked_ids:
                raise HTTPException(status_code=404, detail="目标用户不存在")
            now = datetime.now(timezone.utc)
            if expiry is not None and expiry <= now:
                raise HTTPException(status_code=422, detail="关系有效期必须晚于当前时间")
            existing = session.scalars(select(UserRelationship).where(
                or_(
                    (UserRelationship.subject_user_id == user_id) & (UserRelationship.related_user_id == body.related_user_id),
                    (UserRelationship.subject_user_id == body.related_user_id) & (UserRelationship.related_user_id == user_id),
                ),
                UserRelationship.relationship_type == body.relationship_type,
                UserRelationship.status.in_(["pending", "active"]),
                or_(UserRelationship.valid_until.is_(None), UserRelationship.valid_until > now),
                UserRelationship.is_del.is_(False),
            )).all()
            if body.relationship_type == "family":
                groups: dict[uuid.UUID, list[UserRelationship]] = defaultdict(list)
                for row in existing:
                    groups[row.relationship_pair_id].append(row)
                duplicate = any(row.status == "pending" for row in existing) or any(
                    self.is_confirmed_family_pair(group, now) for group in groups.values()
                )
            else:
                duplicate = bool(existing)
            if duplicate:
                raise HTTPException(status_code=409, detail="相同关系已存在或正在等待确认")
            row = UserRelationship(
                relationship_pair_id=uuid.uuid4(),
                subject_user_id=user_id,
                related_user_id=body.related_user_id,
                relationship_type=body.relationship_type,
                relation_code=body.relation_code,
                relationship_label=body.relationship_label,
                status="pending",
                authorization_method="in_app",
                initiated_by_user_id=user_id,
                valid_until=expiry,
                created_by=user_id,
                updated_by=user_id,
            )
            session.add(row)
            session.flush()
        return row

    def accept(self, session: Session, user: User, relationship_id: int) -> list[UserRelationship]:
        session.rollback()
        with session.begin():
            row = session.scalar(select(UserRelationship).where(
                UserRelationship.id == relationship_id,
                UserRelationship.related_user_id == user.id,
                UserRelationship.is_del.is_(False),
            ).with_for_update())
            if row is None:
                raise self._not_found()
            if row.status != "pending":
                raise HTTPException(status_code=409, detail="该邀请已处理")
            now = datetime.now(timezone.utc)
            if self._effective_status(row, now) == "expired":
                raise HTTPException(status_code=409, detail="关系邀请已过期")
            inverse_code = RECIPROCAL_CODES.get(row.relation_code)
            if row.relationship_type == "family" and inverse_code is None:
                raise HTTPException(status_code=409, detail="亲属关系方向无效，请重新发起邀请")

            row.status = "active"
            row.accepted_at = now
            row.valid_from = now
            row.updated_at = now
            row.updated_by = user.id
            result = [row]
            if row.relationship_type == "family":
                inverse = UserRelationship(
                    relationship_pair_id=row.relationship_pair_id,
                    subject_user_id=row.related_user_id,
                    related_user_id=row.subject_user_id,
                    relationship_type=row.relationship_type,
                    relation_code=inverse_code,
                    relationship_label=None,
                    status="active",
                    authorization_method=row.authorization_method,
                    authorization_reference=row.authorization_reference,
                    initiated_by_user_id=row.initiated_by_user_id,
                    verified_by_user_id=user.id,
                    valid_from=now,
                    valid_until=row.valid_until,
                    accepted_at=now,
                    created_by=user.id,
                    updated_by=user.id,
                )
                session.add(inverse)
                session.flush()
                result.append(inverse)
        return result

    def reject(self, session: Session, user: User, relationship_id: int) -> UserRelationship:
        session.rollback()
        with session.begin():
            row = session.scalar(select(UserRelationship).where(
                UserRelationship.id == relationship_id,
                UserRelationship.related_user_id == user.id,
                UserRelationship.is_del.is_(False),
            ).with_for_update())
            if row is None:
                raise self._not_found()
            if row.status != "pending":
                raise HTTPException(status_code=409, detail="该邀请已处理")
            if self._effective_status(row, datetime.now(timezone.utc)) == "expired":
                raise HTTPException(status_code=409, detail="关系邀请已过期")
            row.status = "rejected"
            row.updated_by = user.id
            row.updated_at = datetime.now(timezone.utc)
        return row

    def revoke(self, session: Session, user: User, relationship_id: int) -> list[UserRelationship]:
        session.rollback()
        with session.begin():
            user_id = user.id
            pair_id = session.scalar(select(UserRelationship.relationship_pair_id).where(
                UserRelationship.id == relationship_id,
                or_(UserRelationship.subject_user_id == user_id, UserRelationship.related_user_id == user_id),
                UserRelationship.is_del.is_(False),
            ))
            if pair_id is None:
                raise self._not_found()
            # Acquire the group locks in the same order, regardless of which row was requested.
            pair = list(session.scalars(select(UserRelationship).where(
                UserRelationship.relationship_pair_id == pair_id,
                UserRelationship.is_del.is_(False),
            ).order_by(UserRelationship.id).with_for_update()).all())
            row = next((member for member in pair if member.id == relationship_id), None)
            if row is None or user_id not in (row.subject_user_id, row.related_user_id):
                raise self._not_found()
            now = datetime.now(timezone.utc)
            if self._effective_status(row, now) != "active":
                raise HTTPException(status_code=409, detail="只有有效关系可以撤销")
            if len(pair) > 1 and not self.is_confirmed_family_pair(pair, now):
                raise HTTPException(status_code=409, detail="关系组记录不一致")
            pair = [member for member in pair if member.status == "active"]
            for member in pair:
                member.status = "revoked"
                member.revoked_at = now
                member.updated_at = now
                member.updated_by = user_id
            return pair
