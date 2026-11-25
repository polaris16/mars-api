"""[新增] 当前用户的系统话题推荐业务逻辑。"""

from sqlalchemy import case, exists, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import InterestDomain, Topic, TopicInterest, TopicParticipant, User, UserInterest
from app.schemas.topic import (
    TopicCardResponse,
    TopicRecommendationResponse,
    TopicRecommendationSort,
)


class TopicService:
    """按兴趣优先、通用话题兜底的顺序提供分页推荐。"""

    @staticmethod
    def record_participation(session: Session, topic_id: int, user_id: int) -> None:
        """Record a first contribution within the caller's fragment transaction."""
        inserted_topic_id = session.scalar(
            insert(TopicParticipant)
            .from_select(
                ["topic_id", "user_id", "created_by", "updated_by"],
                select(Topic.id, literal(user_id), literal(user_id), literal(user_id)).where(
                    Topic.id == topic_id,
                    Topic.is_enabled.is_(True),
                    Topic.is_del.is_(False),
                ),
            )
            .on_conflict_do_nothing(index_elements=["topic_id", "user_id"])
            .returning(TopicParticipant.topic_id)
        )
        if inserted_topic_id is not None:
            session.execute(
                update(Topic)
                .where(Topic.id == inserted_topic_id)
                .values(
                    participant_count=Topic.participant_count + 1,
                    updated_by=user_id,
                    updated_at=func.current_timestamp(),
                )
            )

    @staticmethod
    def _interest_match(user_id: int):
        """判断话题是否关联当前用户已选且仍有效的兴趣领域。"""
        return exists(
            select(1)
            .select_from(TopicInterest)
            .join(UserInterest, UserInterest.interest_id == TopicInterest.interest_id)
            .join(InterestDomain, InterestDomain.id == TopicInterest.interest_id)
            .where(
                TopicInterest.topic_id == Topic.id,
                TopicInterest.is_del.is_(False),
                UserInterest.user_id == user_id,
                UserInterest.is_del.is_(False),
                InterestDomain.is_enabled.is_(True),
                InterestDomain.is_del.is_(False),
            )
        )

    def get_recommended_topics(
        self,
        session: Session,
        user: User,
        page: int,
        page_size: int,
        *,
        paginate: bool = True,
        sort: TopicRecommendationSort = "default",
    ) -> TopicRecommendationResponse:
        """分页读取兴趣匹配话题及通用话题，兴趣话题优先展示。"""
        interest_match = self._interest_match(user.id)
        available_topic_filter = (
            Topic.is_enabled.is_(True),
            Topic.is_del.is_(False),
            or_(interest_match, Topic.is_general.is_(True)),
        )

        # [删除-旧] total = session.scalar(select(func.count(Topic.id)).where(*available_topic_filter)) or 0

        is_interest_source = case(
            (interest_match, 0),
            else_=1,
        )
        source = case(
            (interest_match, literal("interest")),
            else_=literal("general"),
        ).label("recommendation_source")
        # 组内排序后的同值结果继续按配置顺序和 ID 排列。
        sort_key = {
            "default": Topic.participant_count.desc(),
            "participant_count_asc": Topic.participant_count.asc(),
            "participant_count_desc": Topic.participant_count.desc(),
            "title_asc": func.lower(Topic.title).asc(),
            "title_desc": func.lower(Topic.title).desc(),
        }[sort]
        query = (
            select(Topic, source)
            .where(*available_topic_filter)
            .order_by(is_interest_source.asc(), sort_key, Topic.sort_order.asc(), Topic.id.asc())
        )
        if paginate:
            total = session.scalar(select(func.count(Topic.id)).where(*available_topic_filter)) or 0
            rows = session.execute(query.offset((page - 1) * page_size).limit(page_size))
        else:
            rows = session.execute(query).all()
            page, page_size, total = 1, len(rows), len(rows)

        items = [
            TopicCardResponse(
                id=topic.id,
                title=topic.title,
                icon_key=topic.icon_key,
                summary=topic.summary,
                prompt=topic.prompt,
                category=topic.category,
                life_stage=topic.life_stage,
                participant_count=topic.participant_count,
                recommendation_source=recommendation_source,
            )
            for topic, recommendation_source in rows
        ]
        return TopicRecommendationResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
        )
