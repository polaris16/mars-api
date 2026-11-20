"""统一汇总 API 路由。"""

from fastapi import APIRouter
from app.api.endpoints.auth import router as auth_router
from app.api.endpoints.health import router as health_router
from app.api.endpoints.interests import router as interests_router
from app.api.endpoints.topics import router as topics_router
from app.api.endpoints.users import router as users_router
from app.api.endpoints.user_relationships import router as user_relationships_router
from app.api.endpoints.memories import router as memories_router
from app.api.endpoints.memory_extraction import router as memory_extraction_router
from app.api.endpoints.memory_processing import router as memory_processing_router
# [新增] 第四阶段回忆录核对接口。
from app.api.endpoints.memory_review import router as memory_review_router
# [新增] 第五阶段图谱只读接口。
from app.api.endpoints.memory_graph import router as memory_graph_router
from app.api.endpoints.memory_contributors import router as memory_contributors_router
from app.api.endpoints.memory_session_members import router as memory_session_members_router

api_router = APIRouter()
api_router.include_router(auth_router)  # 注册认证接口。
api_router.include_router(health_router)
api_router.include_router(interests_router)
api_router.include_router(topics_router)    # 首页推荐话题路由。
api_router.include_router(users_router)
api_router.include_router(user_relationships_router)
api_router.include_router(memories_router)  # 注册录音与素材管理路由。
api_router.include_router(memory_extraction_router)
api_router.include_router(memory_processing_router)
api_router.include_router(memory_review_router)  # [新增] 注册回忆录级核对与复验。
api_router.include_router(memory_graph_router)
api_router.include_router(memory_session_members_router)
api_router.include_router(memory_contributors_router)
