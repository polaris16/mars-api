"""[新增] 首页推荐话题接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.db.database import get_db
from app.schemas.topic import TopicRecommendationResponse, TopicRecommendationSort
from app.services.topic_service import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


# [新增] 登录用户按兴趣获取分页话题；每页最多 20 条。
@router.get("/recommended", response_model=TopicRecommendationResponse, summary="获取推荐话题")
def get_recommended_topics(
    current_user: CurrentUser,
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[TopicService, Depends(TopicService)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=20)] = 6,
    # [新增] 全量和排序仍使用相同的推荐范围。
    paginate: Annotated[bool, Query(description="设为 false 时返回全部匹配话题")] = True,
    sort: Annotated[
        TopicRecommendationSort, Query(description="只调整兴趣匹配组和通用组各自的顺序")
    ] = "default",
) -> TopicRecommendationResponse:
    # [删除-旧] return service.get_recommended_topics(session, current_user, page, page_size)
    return service.get_recommended_topics(
        session, current_user, page, page_size, paginate=paginate, sort=sort
    )
