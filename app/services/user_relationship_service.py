"""[新增] 管理关系邀请、双向确认及 App 联系人查询。"""

# [删除-旧] """Relationship invitation lifecycle and reciprocal family records."""

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import ColumnElement
from sqlalchemy.sql.selectable import Subquery

from app.models import MemoryContributor, MemorySession, User, UserRelationship
from app.schemas.list_sort import CreatedAtSort
from app.schemas.user_relationship import (
    ContactListResponse,
    ContactProfile,
    ContactRecordResponse,
    ContactRequestDirection,
    ContactRequestItem,
    ContactRequestListResponse,
    ContactRequestStatus,
    ContactSearchRequest,
    ContactSearchResponse,
    ContactView,
    RelationshipCreateRequest,
    RelationshipListResponse,
    RelationshipRecordResponse,
    RelationshipSort,
)
from app.services.memory_validation_queue import schedule_material_validation

RECIPROCAL_CODES = {
    "parent_of": "child_of",
    "child_of": "parent_of",
    "spouse_of": "spouse_of",
    "sibling_of": "sibling_of",
    "related_to": "related_to",
}
# 联系人双方使用相同关系码，亲属仍使用既有的反向映射。
RECIPROCAL_TYPES = {"family": RECIPROCAL_CODES, "contact": {"contact_of": "contact_of"}}
ALLOWED_CODES = {
    "family": set(RECIPROCAL_CODES),
    "agent": {"agent_for"},
    "caregiver": {"caregiver_for"},
    "other": {"other"},
    "contact": set(RECIPROCAL_TYPES["contact"]),
}
INVITATION_TTL = timedelta(days=7)


class UserRelationshipService:
    @staticmethod
    def _not_found() -> HTTPException:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关系不存在")

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return (
            value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        )

    @classmethod
    def _effective_status(cls, row: UserRelationship, now: datetime) -> str:
        # 邀请等待时间与关系有效期分开，确认后只使用关系有效期。
        if row.status == "pending" and row.invitation_expires_at is not None:
            if cls._utc(row.invitation_expires_at) <= now:
                return "expired"
        if row.status in {"pending", "active"} and row.valid_until is not None:
            if cls._utc(row.valid_until) <= now:
                return "expired"
        return row.status

    @classmethod
    def is_confirmed_family_pair(cls, rows: list[UserRelationship], now: datetime) -> bool:
        # public 会话继续使用既有亲属范围。
        return bool(rows and rows[0].relationship_type == "family") and cls.is_confirmed_pair(
            rows, now
        )

    # 会话授权和关系生命周期复用同一个双向确认规则。
    @classmethod
    def is_confirmed_pair(cls, rows: list[UserRelationship], now: datetime) -> bool:
        if len(rows) != 2:
            return False
        first, second = rows
        codes = RECIPROCAL_TYPES.get(first.relationship_type, {})
        return (
            all(
                row.relationship_type == first.relationship_type
                and cls._effective_status(row, now) == "active"
                for row in rows
            )
            and first.subject_user_id == second.related_user_id
            and first.related_user_id == second.subject_user_id
            and codes.get(first.relation_code) == second.relation_code
        )

    def list_for_user(
        self,
        session: Session,
        user: User,
        page: int,
        page_size: int,
        *,
        paginate: bool = True,
        sort: RelationshipSort = "default",
    ) -> RelationshipListResponse:
        user_id = user.id
        visible_groups = select(UserRelationship.relationship_pair_id).where(
            or_(
                UserRelationship.subject_user_id == user_id,
                UserRelationship.related_user_id == user_id,
            ),
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
        # 昵称排序在切页前使用调用者可见方向的对方昵称。
        total = session.scalar(select(func.count()).select_from(group_order)) if paginate else 0
        if sort != "default":
            relation = UserRelationship
            subject_is_user = relation.subject_user_id == user_id
            counterpart_id = (
                select(
                    case(
                        (subject_is_user, relation.related_user_id), else_=relation.subject_user_id
                    )
                )
                .where(
                    relation.relationship_pair_id == group_order.c.relationship_pair_id,
                    or_(subject_is_user, relation.related_user_id == user_id),
                    relation.is_del.is_(False),
                )
                .order_by(subject_is_user.desc(), relation.id.desc())
                .limit(1)
                .correlate(group_order)
                .scalar_subquery()
            )
            group_order = (
                select(group_order, self._nickname_key().label("nickname"))
                .outerjoin(User, and_(User.id == counterpart_id, User.is_del.is_(False)))
                .subquery()
            )
        group_query = select(group_order).order_by(*self._group_order(group_order, sort))
        if paginate:
            group_query = group_query.offset((page - 1) * page_size).limit(page_size)
        page_groups = group_query.subquery()
        rows = list(
            session.scalars(
                select(UserRelationship)
                .join(
                    page_groups,
                    page_groups.c.relationship_pair_id == UserRelationship.relationship_pair_id,
                )
                .where(
                    UserRelationship.is_del.is_(False),
                )
                .order_by(
                    *self._group_order(page_groups, sort),
                    UserRelationship.id.desc(),
                )
            ).all()
        )
        # 双向关系返回当前用户方向。待确认邀请和单向关系只返回已保存的一条。
        groups: dict[uuid.UUID, list[UserRelationship]] = defaultdict(list)
        for row in rows:
            groups[row.relationship_pair_id].append(row)
        now = datetime.now(timezone.utc)
        result = []
        for group in groups.values():
            visible = [
                item for item in group if user_id in (item.subject_user_id, item.related_user_id)
            ]
            row = next((item for item in visible if item.subject_user_id == user_id), visible[0])
            status = self._effective_status(row, now)
            # [删除-旧] if row.relationship_type == "family" and status == "active":
            if row.relationship_type in RECIPROCAL_TYPES and status == "active":
                # [删除-旧] if not self.is_confirmed_family_pair(group, now):
                if not self.is_confirmed_pair(group, now):
                    status = "requires_confirmation"
            result.append(
                RelationshipRecordResponse.model_validate(row).model_copy(update={"status": status})
            )
        if not paginate:
            page, page_size, total = 1, len(result), len(result)
        return RelationshipListResponse(
            items=result, page=page, page_size=page_size, total=total or 0
        )

    # 显式昵称排序忽略大小写，空昵称始终放在最后。
    @staticmethod
    def _nickname_key() -> ColumnElement[str]:
        return func.lower(func.nullif(func.trim(User.display_name), ""))

    @staticmethod
    def _nickname_order(nickname: ColumnElement[str], sort: RelationshipSort) -> ColumnElement[str]:
        return (nickname.desc() if sort == "name_desc" else nickname.asc()).nulls_last()

    @classmethod
    def _group_order(
        cls, groups: Subquery, sort: RelationshipSort
    ) -> tuple[ColumnElement, ColumnElement]:
        if sort != "default":
            return cls._nickname_order(groups.c.nickname, sort), groups.c.latest_id.desc()
        return groups.c.latest_created_at.desc(), groups.c.latest_id.desc()

    # 列表、候选和申请历史使用相同的等待期限与关系期限。
    @staticmethod
    def _invitation_in_time(now: datetime) -> ColumnElement[bool]:
        relation = UserRelationship
        return and_(
            or_(relation.valid_until.is_(None), relation.valid_until > now),
            or_(relation.invitation_expires_at.is_(None), relation.invitation_expires_at > now),
        )

    @staticmethod
    def _current_contact(now: datetime) -> ColumnElement[bool]:
        relation = UserRelationship
        inverse = aliased(relation)
        return and_(
            relation.status == "active",
            or_(relation.valid_from.is_(None), relation.valid_from <= now),
            or_(relation.valid_until.is_(None), relation.valid_until > now),
            select(inverse.id)
            .where(
                inverse.relationship_pair_id == relation.relationship_pair_id,
                inverse.subject_user_id == relation.related_user_id,
                inverse.related_user_id == relation.subject_user_id,
                inverse.relationship_type == "contact",
                inverse.relation_code == "contact_of",
                inverse.status == "active",
                inverse.is_del.is_(False),
                or_(inverse.valid_from.is_(None), inverse.valid_from <= now),
                or_(inverse.valid_until.is_(None), inverse.valid_until > now),
            )
            .exists(),
        )

    # 联系人与待处理申请按生效条件过滤，再计算总数和分页。
    def list_contacts(
        self,
        session: Session,
        user_id: int,
        view: ContactView,
        page: int,
        page_size: int,
        keyword: str | None,
        *,
        paginate: bool = True,
        sort: RelationshipSort = "default",
    ) -> ContactListResponse:
        now = datetime.now(timezone.utc)
        relation = UserRelationship
        incoming = view == "incoming"
        own_id = relation.related_user_id if incoming else relation.subject_user_id
        contact_id = relation.subject_user_id if incoming else relation.related_user_id
        query = (
            select(relation, User.id, User.display_name, User.avatar_url)
            .join(User, User.id == contact_id)
            .where(
                own_id == user_id,
                relation.relationship_type == "contact",
                relation.relation_code == "contact_of",
                relation.is_del.is_(False),
                User.is_del.is_(False),
            )
        )
        if view == "active":
            query = query.where(self._current_contact(now)).order_by(
                func.lower(User.display_name).asc().nulls_last(), relation.id.asc()
            )
        else:
            query = query.where(
                relation.status == "pending",
                self._invitation_in_time(now),
            ).order_by(relation.created_at.desc(), relation.id.desc())
        if keyword and (keyword := keyword.strip()):
            query = query.where(User.display_name.icontains(keyword, autoescape=True))

        # 查询生效的联系人时，先按指定昵称顺序排列，再决定是否切页。
        if view == "active" and sort != "default":
            query = query.order_by(None).order_by(
                self._nickname_order(self._nickname_key(), sort), relation.id.asc()
            )
        total = 0
        if paginate:
            total = session.scalar(
                select(func.count()).select_from(query.order_by(None).subquery())
            )
            query = query.offset((page - 1) * page_size).limit(page_size)
        rows = session.execute(query)
        items = [
            ContactRecordResponse(
                **RelationshipRecordResponse.model_validate(row).model_dump(),
                contact_user=ContactProfile(id=contact_id, display_name=name, avatar_url=avatar),
            )
            for row, contact_id, name, avatar in rows
        ]
        # 全量响应沿用分页结构，计数取实际结果。
        if not paginate:
            page, page_size, total = 1, len(items), len(items)
        return ContactListResponse(items=items, page=page, page_size=page_size, total=total or 0)

    # 只投影供 App 选择目标所需的资料，关系过滤先于分页。
    def search_contact_candidates(
        self, session: Session, user_id: int, body: ContactSearchRequest
    ) -> ContactSearchResponse:
        if not body.keyword:
            return ContactSearchResponse(
                items=[], page=body.page, page_size=body.page_size, total=0
            )

        now = datetime.now(timezone.utc)
        relation = UserRelationship
        blocked = (
            select(relation.id)
            .where(
                relation.relationship_type == "contact",
                relation.relation_code == "contact_of",
                relation.is_del.is_(False),
                or_(
                    and_(relation.subject_user_id == user_id, relation.related_user_id == User.id),
                    and_(relation.subject_user_id == User.id, relation.related_user_id == user_id),
                ),
                or_(
                    self._current_contact(now),
                    and_(relation.status == "pending", self._invitation_in_time(now)),
                ),
            )
            .exists()
        )
        phone_match = User.phone == body.keyword
        matches = phone_match
        if len(body.keyword) >= 2:
            matches = or_(phone_match, User.display_name.icontains(body.keyword, autoescape=True))
        query = select(User.id, User.display_name, User.avatar_url).where(
            User.is_del.is_(False), User.id != user_id, matches, ~blocked
        )
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        # 手机号精确命中优先，显式排序只改变组内昵称顺序。
        nickname_order = (
            func.lower(User.display_name).asc().nulls_last()
            if body.sort == "default"
            else self._nickname_order(self._nickname_key(), body.sort)
        )
        rows = session.execute(
            query.order_by(
                phone_match.desc(),
                nickname_order,
                User.id,
            )
            .offset((body.page - 1) * body.page_size)
            .limit(body.page_size)
        )
        return ContactSearchResponse(
            items=[ContactProfile.model_validate(row) for row in rows],
            page=body.page,
            page_size=body.page_size,
            total=total or 0,
        )

    # 只取发起方向，已通过的申请在解除好友后仍保留原处理结果。
    def list_contact_requests(
        self,
        session: Session,
        user_id: int,
        direction: ContactRequestDirection,
        desired_statuses: tuple[ContactRequestStatus, ...],
        page: int,
        page_size: int,
        *,
        sort: CreatedAtSort = "default",
    ) -> ContactRequestListResponse:
        now = datetime.now(timezone.utc)
        relation = UserRelationship
        incoming = direction == "incoming"
        own_id = relation.related_user_id if incoming else relation.subject_user_id
        other_id = relation.subject_user_id if incoming else relation.related_user_id
        request_status = case(
            (or_(relation.accepted_at.is_not(None), relation.status == "active"), "accepted"),
            (relation.status == "revoked", "withdrawn"),
            (and_(relation.status == "pending", ~self._invitation_in_time(now)), "expired"),
            else_=relation.status,
        ).label("request_status")
        resolved_at = case(
            (relation.accepted_at.is_not(None), relation.accepted_at),
            (relation.status == "rejected", relation.updated_at),
            (relation.status == "revoked", relation.revoked_at),
        ).label("resolved_at")
        query = (
            select(
                relation.id,
                relation.request_message,
                request_status,
                relation.created_at,
                resolved_at,
                other_id.label("contact_user_id"),
                User.display_name,
                User.avatar_url,
            )
            .outerjoin(User, and_(User.id == other_id, User.is_del.is_(False)))
            .where(
                own_id == user_id,
                relation.initiated_by_user_id == relation.subject_user_id,
                relation.relationship_type == "contact",
                relation.relation_code == "contact_of",
                relation.authorization_method == "in_app",
                relation.is_del.is_(False),
            )
        )
        # 分页前按对外状态筛选，自动过期的申请归入 expired。
        query = query.where(request_status.in_(desired_statuses))
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        created_at = (
            relation.created_at.asc() if sort == "created_at_asc" else relation.created_at.desc()
        )
        rows = session.execute(
            query.order_by(created_at, relation.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return ContactRequestListResponse(
            items=[
                ContactRequestItem(
                    request_id=row.id,
                    message=row.request_message,
                    status=row.request_status,
                    created_at=self._utc(row.created_at),
                    resolved_at=self._utc(row.resolved_at) if row.resolved_at is not None else None,
                    contact_user=ContactProfile(
                        id=row.contact_user_id,
                        display_name=row.display_name,
                        avatar_url=row.avatar_url,
                    ),
                )
                for row in rows
            ],
            page=page,
            page_size=page_size,
            total=total or 0,
        )

    def create(
        self,
        session: Session,
        user: User,
        body: RelationshipCreateRequest,
        *,
        request_message: str | None = None,
    ) -> UserRelationship:
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
            locked_ids = set(
                session.scalars(
                    select(User.id)
                    .where(User.id.in_((user_id, body.related_user_id)), User.is_del.is_(False))
                    .order_by(User.id)
                    .with_for_update()
                ).all()
            )
            if user_id not in locked_ids:
                raise HTTPException(status_code=401, detail="用户不存在或已停用")
            if body.related_user_id not in locked_ids:
                raise HTTPException(status_code=404, detail="目标用户不存在")
            now = datetime.now(timezone.utc)
            if expiry is not None and expiry <= now:
                raise HTTPException(status_code=422, detail="关系有效期必须晚于当前时间")
            existing = session.scalars(
                select(UserRelationship).where(
                    or_(
                        (UserRelationship.subject_user_id == user_id)
                        & (UserRelationship.related_user_id == body.related_user_id),
                        (UserRelationship.subject_user_id == body.related_user_id)
                        & (UserRelationship.related_user_id == user_id),
                    ),
                    UserRelationship.relationship_type == body.relationship_type,
                    UserRelationship.status.in_(["pending", "active"]),
                    or_(UserRelationship.valid_until.is_(None), UserRelationship.valid_until > now),
                    or_(
                        UserRelationship.status != "pending",
                        UserRelationship.invitation_expires_at.is_(None),
                        UserRelationship.invitation_expires_at > now,
                    ),
                    UserRelationship.is_del.is_(False),
                )
            ).all()
            if body.relationship_type in RECIPROCAL_TYPES:
                groups: dict[uuid.UUID, list[UserRelationship]] = defaultdict(list)
                for row in existing:
                    groups[row.relationship_pair_id].append(row)
                duplicate = any(row.status == "pending" for row in existing) or any(
                    self.is_confirmed_pair(group, now)
                    for group in groups.values()
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
                request_message=request_message,
                status="pending",
                authorization_method="in_app",
                initiated_by_user_id=user_id,
                valid_until=expiry,
                invitation_expires_at=min(now + INVITATION_TTL, expiry)
                if expiry is not None
                else now + INVITATION_TTL,
                created_by=user_id,
                updated_by=user_id,
            )
            session.add(row)
            session.flush()
        return row

    def accept(self, session: Session, user: User, relationship_id: int) -> list[UserRelationship]:
        session.rollback()
        with session.begin():
            row = session.scalar(
                select(UserRelationship)
                .where(
                    UserRelationship.id == relationship_id,
                    UserRelationship.related_user_id == user.id,
                    UserRelationship.is_del.is_(False),
                )
                .with_for_update()
            )
            if row is None:
                raise self._not_found()
            if row.status != "pending":
                raise HTTPException(status_code=409, detail="该邀请已处理")
            now = datetime.now(timezone.utc)
            if self._effective_status(row, now) == "expired":
                raise HTTPException(status_code=409, detail="关系邀请已过期")
            codes = RECIPROCAL_TYPES.get(row.relationship_type)
            inverse_code = codes.get(row.relation_code) if codes else None
            if codes is not None and inverse_code is None:
                raise HTTPException(status_code=409, detail="双向关系方向无效，请重新发起邀请")

            row.status = "active"
            row.accepted_at = now
            row.valid_from = now
            # 好友确认后不再沿用申请截止时间，旧入口的期限只约束等待确认。
            if row.relationship_type == "contact":
                row.invitation_expires_at = None
                row.valid_until = None
            row.updated_at = now
            row.updated_by = user.id
            result = [row]
            if inverse_code is not None:
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
            row = session.scalar(
                select(UserRelationship)
                .where(
                    UserRelationship.id == relationship_id,
                    UserRelationship.related_user_id == user.id,
                    UserRelationship.is_del.is_(False),
                )
                .with_for_update()
            )
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
            pair_id = session.scalar(
                select(UserRelationship.relationship_pair_id).where(
                    UserRelationship.id == relationship_id,
                    or_(
                        UserRelationship.subject_user_id == user_id,
                        UserRelationship.related_user_id == user_id,
                    ),
                    UserRelationship.is_del.is_(False),
                )
            )
            if pair_id is None:
                raise self._not_found()
            # 与接受邀请共用原邀请行的锁，再用新查询读出可能刚写入的反向关系。
            session.scalar(
                select(UserRelationship.id)
                .where(
                    UserRelationship.relationship_pair_id == pair_id,
                    UserRelationship.is_del.is_(False),
                )
                .order_by(UserRelationship.id)
                .limit(1)
                .with_for_update()
            )
            # Acquire the group locks in the same order, regardless of which row was requested.
            pair = list(
                session.scalars(
                    select(UserRelationship)
                    .where(
                        UserRelationship.relationship_pair_id == pair_id,
                        UserRelationship.is_del.is_(False),
                    )
                    .order_by(UserRelationship.id)
                    .with_for_update()
                ).all()
            )
            row = next((member for member in pair if member.id == relationship_id), None)
            if row is None or user_id not in (row.subject_user_id, row.related_user_id):
                raise self._not_found()
            now = datetime.now(timezone.utc)
            current_status = self._effective_status(row, now)
            if current_status == "pending":
                # 发起方可撤回邀请，接收方仍通过拒绝接口处理。
                if row.initiated_by_user_id != user_id:
                    raise HTTPException(status_code=403, detail="只有发起人可以撤回邀请")
                if len(pair) != 1:
                    raise HTTPException(status_code=409, detail="邀请组记录不一致")
            elif current_status == "active":
                if len(pair) > 1 and not self.is_confirmed_pair(pair, now):
                    raise HTTPException(status_code=409, detail="关系组记录不一致")
                pair = [member for member in pair if member.status == "active"]
            else:
                raise HTTPException(status_code=409, detail="只有待确认邀请或有效关系可以撤销")
            for member in pair:
                member.status = "revoked"
                member.revoked_at = now
                member.updated_at = now
                member.updated_by = user_id
            # 一次关系撤回可影响两位所有者的素材，按固定顺序登记复验。
            if current_status == "active" and row.relationship_type in RECIPROCAL_TYPES:
                affected = session.execute(
                    select(MemorySession.owner_user_id, MemorySession.id)
                    .join(
                        MemoryContributor,
                        MemoryContributor.session_id == MemorySession.id,
                    )
                    .where(
                        MemorySession.visibility.is_not(None),
                        MemorySession.is_del.is_(False),
                        MemoryContributor.relationship_id.in_([member.id for member in pair]),
                        MemoryContributor.is_del.is_(False),
                    )
                    .distinct()
                    .order_by(MemorySession.owner_user_id, MemorySession.id)
                )
                for owner_id, session_id in affected:
                    schedule_material_validation(
                        session,
                        owner_id,
                        session_id,
                        actor_id=user_id,
                        reason="source_relationship_revoked",
                    )
            return pair
