"""兴趣领域接口的请求与响应模型。"""

from pydantic import BaseModel, Field


class InterestResponse(BaseModel):
    id: int
    code: str
    name: str
    sort_order: int


class UserInterestsUpdateRequest(BaseModel):
    # 不限制兴趣数量；由后端校验 ID 有效且无重复。
    interest_ids: list[int] = Field()


class UserInterestsResponse(BaseModel):
    interest_ids: list[int]
    interests: list[InterestResponse]
