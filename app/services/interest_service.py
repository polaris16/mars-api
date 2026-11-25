"""兴趣领域目录和用户选择业务逻辑。"""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InterestDomain, User, UserInterest
from app.schemas.interest import InterestResponse


class InterestService:
    def list_available(self, session: Session) -> list[InterestDomain]:
        """返回启用且未删除的兴趣领域，按配置顺序排列。"""
        return list(
            session.scalars(
                select(InterestDomain)
                .where(InterestDomain.is_enabled.is_(True), InterestDomain.is_del.is_(False))
                .order_by(InterestDomain.sort_order, InterestDomain.id)
            ).all()
        )

    def get_user_interests(self, session: Session, user: User) -> list[InterestDomain]:
        """读取用户当前有效选择，并忽略已停用或已删除的领域。"""
        return list(
            session.scalars(
                select(InterestDomain)
                .join(UserInterest, UserInterest.interest_id == InterestDomain.id)
                .where(
                    UserInterest.user_id == user.id,
                    UserInterest.is_del.is_(False),
                    InterestDomain.is_enabled.is_(True),
                    InterestDomain.is_del.is_(False),
                )
                .order_by(InterestDomain.sort_order, InterestDomain.id)
            ).all()
        )

    def save_user_interests(
        self, session: Session, user: User, interest_ids: list[int]
    ) -> list[InterestDomain]:
        """以请求列表替换用户选择；关联通过软删除和恢复实现可重复选择。"""
        if len(interest_ids) != len(set(interest_ids)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="兴趣领域 ID 不能重复"
            )

        requested_ids = set(interest_ids)
        with session.begin():
            # 先锁用户，再读旧兴趣，保证同一用户的两次替换依次执行。
            current_user = session.scalar(
                select(User)
                .where(User.id == user.id, User.is_del.is_(False))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if current_user is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用"
                )

            valid_ids = set(
                session.scalars(
                    select(InterestDomain.id).where(
                        InterestDomain.id.in_(requested_ids),
                        InterestDomain.is_enabled.is_(True),
                        InterestDomain.is_del.is_(False),
                    )
                ).all()
            )
            if valid_ids != requested_ids:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="包含无效、停用或已删除的兴趣领域",
                )

            records = list(
                session.scalars(
                    select(UserInterest).where(UserInterest.user_id == current_user.id)
                ).all()
            )
            by_interest_id = {record.interest_id: record for record in records}
            now = datetime.now(timezone.utc)

            for interest_id, record in by_interest_id.items():
                record.is_del = interest_id not in requested_ids
                record.updated_by = current_user.id
                record.updated_at = now

            for interest_id in requested_ids - by_interest_id.keys():
                session.add(
                    UserInterest(
                        user_id=current_user.id,
                        interest_id=interest_id,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    )
                )

            # SessionLocal 关闭了 autoflush；先写出软删除/新增状态，再读取最终选择列表。
            session.flush()

            # 只有必填的基础资料已填写且用户提交了兴趣选择，才完成首次引导。
            if (
                current_user.display_name
                and current_user.sex is not None
                and current_user.birth_date is not None
            ):
                current_user.onboarding_completed = True
                current_user.updated_by = current_user.id
                current_user.updated_at = now

            selected_interests = list(
                session.scalars(
                    select(InterestDomain)
                    .join(UserInterest, UserInterest.interest_id == InterestDomain.id)
                    .where(
                        UserInterest.user_id == current_user.id,
                        UserInterest.is_del.is_(False),
                        InterestDomain.is_enabled.is_(True),
                        InterestDomain.is_del.is_(False),
                    )
                    .order_by(InterestDomain.sort_order, InterestDomain.id)
                ).all()
            )
        return selected_interests

    @staticmethod
    def to_response(interest: InterestDomain) -> InterestResponse:
        return InterestResponse(
            id=interest.id,
            code=interest.code,
            name=interest.name,
            sort_order=interest.sort_order,
        )
