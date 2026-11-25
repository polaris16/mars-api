"""记忆会话成员授权与管理权限。"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import MemorySession, MemorySessionMember, User, UserRelationship
from app.schemas.memory_session_member import (
    MemberGrantRequest,
    MemberPage,
    MemberResponse,
    MemberSort,
)
from app.services.memory_validation_queue import schedule_material_validation
from app.services.user_relationship_service import UserRelationshipService


class MemorySessionMemberService:
    @staticmethod
    def _utc(value: datetime) -> datetime:
        return (
            value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        )

    @classmethod
    def _current_status(cls, member: MemorySessionMember, now: datetime) -> str:
        if member.status == "active" and member.valid_until is not None:
            if cls._utc(member.valid_until) <= now:
                return "expired"
        return member.status

    def _source_rows(
        self,
        session: Session,
        owner_id: int,
        user_id: int,
        role: str,
        relationship_id: int | None,
        now: datetime,
        *,
        allow_contacts: bool = False,
    ) -> list[UserRelationship] | None:
        """Return the relationship rows supporting membership, or None if invalid."""
        if relationship_id is None:
            return [] if role == "viewer" else None
        row = session.scalar(
            select(UserRelationship).where(
                UserRelationship.id == relationship_id,
                UserRelationship.is_del.is_(False),
            )
        )
        if row is None or {row.subject_user_id, row.related_user_id} != {owner_id, user_id}:
            return None
        if role == "delegate":
            if (row.relationship_type, row.relation_code, row.subject_user_id) != (
                "agent",
                "agent_for",
                user_id,
            ):
                return None
            rows = [row]
        else:
            # 仅版本化会话开放联系人授权，沿用原始记忆由所有者编辑的边界。
            allowed = row.relationship_type == "family" or (
                allow_contacts
                and row.relationship_type == "contact"
                and role in {"viewer", "contributor"}
            )
            if not allowed:
                return None
            rows = list(
                session.scalars(
                    select(UserRelationship).where(
                        UserRelationship.relationship_pair_id == row.relationship_pair_id,
                        UserRelationship.is_del.is_(False),
                    )
                )
            )
            if not UserRelationshipService.is_confirmed_pair(rows, now):
                return None
        if any(
            item.status != "active"
            or (item.valid_from is not None and self._utc(item.valid_from) > now)
            or (item.valid_until is not None and self._utc(item.valid_until) <= now)
            for item in rows
        ):
            return None
        return rows

    @staticmethod
    def get_memory_session(session: Session, session_id: int, *, lock: bool) -> MemorySession:
        query = (
            select(MemorySession)
            .join(User, User.id == MemorySession.owner_user_id)
            .where(
                MemorySession.id == session_id,
                MemorySession.is_del.is_(False),
                User.is_del.is_(False),
            )
        )
        if lock:
            # Serialize grants and revocations within the session, including regrants.
            query = query.with_for_update(of=MemorySession)
        memory = session.scalar(query)
        if memory is None:
            raise HTTPException(status_code=404, detail="记忆会话不存在")
        return memory

    def require_manager(
        self,
        session: Session,
        session_id: int,
        user_id: int,
        *,
        lock: bool,
    ) -> tuple[MemorySession, datetime | None]:
        memory = self.get_memory_session(session, session_id, lock=lock)
        if memory.owner_user_id == user_id:
            return memory, None
        if memory.visibility == "secret":
            raise HTTPException(status_code=404, detail="记忆会话不存在")
        if memory.visibility is not None:
            raise HTTPException(status_code=403, detail="只有会话所有者可以管理授权")
        member = session.scalar(
            select(MemorySessionMember).where(
                MemorySessionMember.session_id == session_id,
                MemorySessionMember.user_id == user_id,
                MemorySessionMember.is_del.is_(False),
            )
        )
        if member is None:
            raise HTTPException(status_code=404, detail="记忆会话不存在")
        now = datetime.now(timezone.utc)
        if (
            member.role != "delegate"
            or not member.can_manage_members
            or self._current_status(member, now) != "active"
        ):
            raise HTTPException(status_code=403, detail="没有会话成员管理权限")
        source = self._source_rows(
            session, memory.owner_user_id, user_id, member.role, member.source_relationship_id, now
        )
        if source is None:
            raise HTTPException(status_code=403, detail="代理关系已失效")
        deadlines = [
            self._utc(value)
            for value in (member.valid_until, source[0].valid_until)
            if value is not None
        ]
        return memory, min(deadlines, default=None)

    def is_member_effective(
        self,
        session: Session,
        owner_id: int,
        member: MemorySessionMember,
        now: datetime,
    ) -> bool:
        memory = session.get(MemorySession, member.session_id)
        if memory is None or memory.is_del or memory.visibility == "secret":
            return False
        if memory.visibility is not None and member.source_relationship_id is None:
            return False
        return (
            not member.is_del
            and self._current_status(member, now) == "active"
            and self._source_rows(
                session,
                owner_id,
                member.user_id,
                member.role,
                member.source_relationship_id,
                now,
                allow_contacts=memory.visibility in {"private", "public"},
            )
            is not None
        )

    def _response(
        self,
        session: Session,
        owner_id: int,
        member: MemorySessionMember,
        now: datetime,
        *,
        user_active: bool,
    ) -> MemberResponse:
        status = self._current_status(member, now)
        is_effective = user_active and self.is_member_effective(session, owner_id, member, now)
        response = MemberResponse.model_validate(member)
        return response.model_copy(
            update={
                "status": status,
                "is_effective": is_effective,
                "granted_at": self._utc(response.granted_at),
                "valid_until": self._utc(response.valid_until)
                if response.valid_until is not None
                else None,
                "revoked_at": self._utc(response.revoked_at)
                if response.revoked_at is not None
                else None,
            }
        )

    def list_members(
        self,
        session: Session,
        user: User,
        session_id: int,
        page: int,
        page_size: int,
        *,
        paginate: bool = True,
        sort: MemberSort = "default",
    ) -> MemberPage:
        memory, _ = self.require_manager(session, session_id, user.id, lock=False)
        query = select(MemorySessionMember).where(
            MemorySessionMember.session_id == session_id,
            MemorySessionMember.is_del.is_(False),
        )
        # 全量和分页都使用相同的会话管理权限和记录范围。
        granted_at = MemorySessionMember.granted_at
        ordering = granted_at.asc() if sort == "granted_at_asc" else granted_at.desc()
        listing = (
            query.join(User, User.id == MemorySessionMember.user_id)
            .add_columns(User.is_del)
            .order_by(ordering, MemorySessionMember.id.desc())
        )
        if paginate:
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            listing = listing.offset((page - 1) * page_size).limit(page_size)
        rows = session.execute(listing).all()
        if not paginate:
            page, page_size, total = 1, len(rows), len(rows)
        now = datetime.now(timezone.utc)
        return MemberPage(
            items=[
                self._response(session, memory.owner_user_id, row, now, user_active=not deleted)
                for row, deleted in rows
            ],
            page=page,
            page_size=page_size,
            total=total,
        )

    def grant(
        self, session: Session, user: User, session_id: int, body: MemberGrantRequest
    ) -> MemberResponse:
        with session.begin():
            actor_id = user.id
            memory, manager_deadline = self.require_manager(
                session, session_id, actor_id, lock=True
            )
            if body.user_id == memory.owner_user_id:
                raise HTTPException(status_code=422, detail="会话主人无需成员授权")
            if memory.visibility is not None:
                if body.source_relationship_id is None:
                    raise HTTPException(status_code=422, detail="授权需要有效直接关系")
                if body.role == "editor" or body.can_manage_members:
                    raise HTTPException(status_code=422, detail="本期编辑和成员管理限会话所有者")
            if body.can_manage_members and body.role != "delegate":
                raise HTTPException(status_code=422, detail="只有代理角色可以管理成员")
            if actor_id != memory.owner_user_id and body.role == "delegate":
                raise HTTPException(status_code=403, detail="只有会话主人可以授权代理")
            if (
                session.scalar(
                    select(User.id).where(User.id == body.user_id, User.is_del.is_(False))
                )
                is None
            ):
                raise HTTPException(status_code=404, detail="目标用户不存在或已停用")
            member = session.scalar(
                select(MemorySessionMember).where(
                    MemorySessionMember.session_id == session_id,
                    MemorySessionMember.user_id == body.user_id,
                )
            )
            if (
                member is not None
                and member.role == "delegate"
                and actor_id != memory.owner_user_id
            ):
                raise HTTPException(status_code=403, detail="只有会话主人可以管理代理授权")

            now = datetime.now(timezone.utc)
            deadline = (
                self._utc(body.valid_until) if body.valid_until is not None else manager_deadline
            )
            if deadline is not None and (
                deadline <= now or (manager_deadline is not None and deadline > manager_deadline)
            ):
                raise HTTPException(status_code=422, detail="授权期限已过或超出代理授权期限")
            if (
                self._source_rows(
                    session,
                    memory.owner_user_id,
                    body.user_id,
                    body.role,
                    body.source_relationship_id,
                    now,
                    allow_contacts=memory.visibility in {"private", "public"},
                )
                is None
            ):
                raise HTTPException(status_code=422, detail="缺少与该成员角色匹配的有效用户关系")
            if (
                member is not None
                and not member.is_del
                and self._response(
                    session,
                    memory.owner_user_id,
                    member,
                    now,
                    user_active=True,
                ).is_effective
            ):
                raise HTTPException(status_code=409, detail="该用户已有有效授权，请先撤销")

            if member is None:
                member = MemorySessionMember(
                    session_id=session_id, user_id=body.user_id, created_by=actor_id
                )
                session.add(member)
            member.role = body.role
            member.source_relationship_id = body.source_relationship_id
            member.can_manage_members = body.can_manage_members
            member.status = "active"
            member.is_del = False
            member.granted_by_user_id = actor_id
            member.granted_at = now
            member.valid_until = deadline
            member.revoked_at = None
            member.revocation_reason = None
            member.updated_by = actor_id
            member.updated_at = now
            session.flush()
            # 重新授权的贡献者可能让历史素材恢复为有效证据。
            if memory.visibility is not None and member.role in {"contributor", "editor"}:
                schedule_material_validation(
                    session,
                    memory.owner_user_id,
                    memory.id,
                    actor_id=actor_id,
                    reason="contributor_membership_granted",
                )
            return self._response(session, memory.owner_user_id, member, now, user_active=True)

    def revoke(
        self,
        session: Session,
        user: User,
        session_id: int,
        member_id: int,
        reason: str | None,
    ) -> MemberResponse:
        with session.begin():
            actor_id = user.id
            memory, _ = self.require_manager(session, session_id, actor_id, lock=True)
            member = session.scalar(
                select(MemorySessionMember).where(
                    MemorySessionMember.id == member_id,
                    MemorySessionMember.session_id == session_id,
                    MemorySessionMember.is_del.is_(False),
                )
            )
            if member is None:
                raise HTTPException(status_code=404, detail="会话成员不存在")
            if member.role == "delegate" and actor_id != memory.owner_user_id:
                raise HTTPException(status_code=403, detail="只有会话主人可以撤销代理授权")
            now = datetime.now(timezone.utc)
            if member.status != "revoked":
                member.status = "revoked"
                member.revoked_at = now
                member.revocation_reason = reason
                member.updated_by = actor_id
                member.updated_at = now
                # 贡献者授权撤回后复核该会话已被引用的来源。
                if memory.visibility is not None and member.role in {"contributor", "editor"}:
                    schedule_material_validation(
                        session,
                        memory.owner_user_id,
                        memory.id,
                        actor_id=actor_id,
                        reason="contributor_membership_revoked",
                    )
            return self._response(session, memory.owner_user_id, member, now, user_active=False)
