"""[新增] 推荐话题接口的响应模型。"""

from typing import Literal

from pydantic import BaseModel


class TopicCardResponse(BaseModel):
    """首页话题卡片及进入对话所需的信息。"""

    id: int
    title: str
    icon_key: str
    summary: str
    prompt: str
    category: str
    life_stage: str | None
    participant_count: int
    recommendation_source: Literal["interest", "general"]


class TopicRecommendationResponse(BaseModel):
    """推荐话题分页结果。"""

    items: list[TopicCardResponse]
    page: int
    page_size: int
    total: int
