"""登记记忆素材提供者并记录本人同意。"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import MemoryContributor, MemorySession, MemorySessionMember, User
from app.schemas.memory_contributor import ContributorPage, ContributorResponse, ContributorSort
from app.services.memory_session_member_service import MemorySessionMemberService
from app.services.memory_validation_queue import schedule_material_validation


class MemoryContributorService:
    def __init__(self) -> None:
        self.members = MemorySessionMemberService()

    @staticmethod
    def _records(session_id: int):
        return select(MemoryContributor).where(
            MemoryContributor.session_id == session_id,
            MemoryContributor.contributor_type.in_(("owner", "registered_user")),
            MemoryContributor.account_user_id.is_not(None),
            MemoryContributor.is_del.is_(False),
        )

    def _find(self, db: Session, session_id: int, user_id: int) -> MemoryContributor | None:
        return db.scalar(
            self._records(session_id)
            .where(MemoryContributor.account_user_id == user_id)
            .order_by(MemoryContributor.id),
        )

    def _eligible_member(
        self,
        db: Session,
        memory: MemorySession,
        user_id: int,
        now: datetime,
    ) -> MemorySessionMember | None:
        member = db.scalar(
            select(MemorySessionMember).where(
                MemorySessionMember.session_id == memory.id,
                MemorySessionMember.user_id == user_id,
                MemorySessionMember.role.in_(("contributor", "editor")),
                MemorySessionMember.is_del.is_(False),
            )
        )
        if member is not None and self.members.is_member_effective(
            db, memory.owner_user_id, member, now
        ):
            return member
        return None

    def _is_eligible(
        self,
        db: Session,
        memory: MemorySession,
        row: MemoryContributor,
        account: User,
        now: datetime,
    ) -> bool:
        if account.is_del or row.identity_status != "verified":
            return False
        if row.contributor_type == "owner":
            return row.account_user_id == memory.owner_user_id
        member = self._eligible_member(db, memory, account.id, now)
        return (
            member is not None
            and row.session_member_id == member.id
            and row.relationship_id == member.source_relationship_id
        )

    def is_consented_contributor(
        self,
        db: Session,
        memory: MemorySession,
        row: MemoryContributor,
        user: User,
    ) -> bool:
        return (
            row.session_id == memory.id
            and row.account_user_id == user.id
            and not row.is_del
            and row.consent_status == "accepted"
            and self._is_eligible(db, memory, row, user, datetime.now(timezone.utc))
        )

    def require_consented_contributor(
        self,
        db: Session,
        memory: MemorySession,
        user: User,
    ) -> MemoryContributor:
        row = self._find(db, memory.id, user.id)
        if row is None or not self.is_consented_contributor(db, memory, row, user):
            raise HTTPException(status_code=403, detail="需要有效的素材提供者资格和本人同意")
        return row

    @staticmethod
    def _display_name(account: User, contributor_type: str) -> str:
        return account.display_name or ("会话主人" if contributor_type == "owner" else "素材提供者")

    def _response(
        self,
        db: Session,
        memory: MemorySession,
        row: MemoryContributor,
        account: User,
        now: datetime,
    ) -> ContributorResponse:
        response = ContributorResponse.model_validate(row)
        consented_at = row.consented_at
        if consented_at is not None:
            consented_at = (
                consented_at.replace(tzinfo=timezone.utc)
                if consented_at.tzinfo is None
                else consented_at.astimezone(timezone.utc)
            )
        return response.model_copy(
            update={
                "display_name": self._display_name(account, row.contributor_type),
                "consented_at": consented_at,
                "is_eligible": self._is_eligible(db, memory, row, account, now),
            }
        )

    def list_contributors(
        self,
        db: Session,
        user: User,
        session_id: int,
        page: int,
        page_size: int,
        *,
        paginate: bool = True,
        sort: ContributorSort = "default",
    ) -> ContributorPage:
        memory, _ = self.members.require_manager(db, session_id, user.id, lock=False)
        query = self._records(session_id)
        # 全量和分页都使用相同的会话管理权限和记录范围。
        ordering = MemoryContributor.id.desc() if sort == "id_desc" else MemoryContributor.id
        listing = (
            query.join(User, User.id == MemoryContributor.account_user_id)
            .add_columns(User)
            .order_by(ordering)
        )
        if paginate:
            total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            listing = listing.offset((page - 1) * page_size).limit(page_size)
        rows = db.execute(listing).all()
        if not paginate:
            page, page_size, total = 1, len(rows), len(rows)
        now = datetime.now(timezone.utc)
        return ContributorPage(
            items=[self._response(db, memory, row, account, now) for row, account in rows],
            page=page,
            page_size=page_size,
            total=total,
        )

    def register(
        self,
        db: Session,
        user: User,
        session_id: int,
        target_id: int,
    ) -> tuple[ContributorResponse, bool]:
        with db.begin():
            actor_id = user.id
            memory, _ = self.members.require_manager(db, session_id, actor_id, lock=True)
            account = db.scalar(select(User).where(User.id == target_id, User.is_del.is_(False)))
            if account is None:
                raise HTTPException(status_code=404, detail="提供者账号不存在")
            now = datetime.now(timezone.utc)
            owner = target_id == memory.owner_user_id
            member = None if owner else self._eligible_member(db, memory, target_id, now)
            if not owner and member is None:
                raise HTTPException(
                    status_code=422,
                    detail="提供者需要有效的用户关系和会话贡献授权",
                )

            contributor_type = "owner" if owner else "registered_user"
            member_id = member.id if member is not None else None
            relationship_id = member.source_relationship_id if member is not None else None
            row = self._find(db, session_id, target_id)
            created = row is None
            if row is None:
                row = MemoryContributor(
                    session_id=session_id, account_user_id=target_id, created_by=actor_id
                )
                db.add(row)
            binding_changed = (
                row.contributor_type,
                row.session_member_id,
                row.relationship_id,
            ) != (
                contributor_type,
                member_id,
                relationship_id,
            )
            if created or binding_changed:
                row.contributor_type = contributor_type
                row.session_member_id = member_id
                row.relationship_id = relationship_id
                row.display_name = self._display_name(account, contributor_type)
                row.identity_status = "verified"
                row.consent_status = "pending"
                row.consented_at = None
                row.updated_by = actor_id
                row.updated_at = now
                db.flush()
            return self._response(db, memory, row, account, now), created

    def get_my_registration(self, db: Session, user: User, session_id: int) -> ContributorResponse:
        row = self._find(db, session_id, user.id)
        if row is None:
            raise HTTPException(status_code=404, detail="素材提供者不存在")
        memory = self.members.get_memory_session(db, session_id, lock=False)
        return self._response(db, memory, row, user, datetime.now(timezone.utc))

    def set_my_consent(
        self,
        db: Session,
        user: User,
        session_id: int,
        contributor_id: int,
        status: str,
    ) -> ContributorResponse:
        with db.begin():
            actor_id = user.id
            query = self._records(session_id).where(
                MemoryContributor.id == contributor_id,
                MemoryContributor.account_user_id == actor_id,
            )
            if db.scalar(query.with_only_columns(MemoryContributor.id)) is None:
                raise HTTPException(status_code=404, detail="素材提供者不存在")
            memory = self.members.get_memory_session(db, session_id, lock=True)
            row = db.scalar(query)
            if row is None:
                raise HTTPException(status_code=404, detail="素材提供者不存在")
            now = datetime.now(timezone.utc)
            if status == "accepted" and not self._is_eligible(db, memory, row, user, now):
                raise HTTPException(status_code=409, detail="素材提供者资格已失效")
            if row.consent_status != status:
                row.consent_status = status
                if status == "accepted":
                    row.consented_at = now
                row.updated_by = actor_id
                row.updated_at = now
                # 同意状态变更可能使现有叙事证据失效。
                if memory.visibility is not None and row.contributor_type == "registered_user":
                    schedule_material_validation(
                        db,
                        memory.owner_user_id,
                        memory.id,
                        actor_id=actor_id,
                        reason="contributor_consent_changed",
                    )
            return self._response(db, memory, row, user, now)
