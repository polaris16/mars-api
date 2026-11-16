"""[新增] 当前用户的系统话题推荐业务逻辑。"""

from sqlalchemy import case, exists, func, literal, or_, select
from sqlalchemy.orm import Session

from app.models import InterestDomain, Topic, TopicInterest, TUser, UserInterest
from app.schemas.topic import TopicCardResponse, TopicRecommendationResponse


class TopicService:
    """按兴趣优先、通用话题兜底的顺序提供分页推荐。"""

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
        user: TUser,
        page: int,
        page_size: int,
    ) -> TopicRecommendationResponse:
        """分页读取兴趣匹配话题及通用话题，兴趣话题优先展示。"""
        interest_match = self._interest_match(user.id)
        available_topic_filter = (
            Topic.is_enabled.is_(True),
            Topic.is_del.is_(False),
            or_(interest_match, Topic.is_general.is_(True)),
        )

        total = session.scalar(
            select(func.count(Topic.id)).where(*available_topic_filter)
        ) or 0

        is_interest_source = case(
            (interest_match, 0),
            else_=1,
        )
        source = case(
            (interest_match, literal("interest")),
            else_=literal("general"),
        ).label("recommendation_source")
        rows = session.execute(
            select(Topic, source)
            .where(*available_topic_filter)
            .order_by(
                is_interest_source.asc(),
                Topic.participant_count.desc(),
                Topic.sort_order.asc(),
                Topic.id.asc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()

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
