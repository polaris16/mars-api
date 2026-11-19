"""统一汇总 API 路由。"""

from fastapi import APIRouter

# 手机 App 登录与登出路由。
from app.api.endpoints.auth import router as auth_router
from app.api.endpoints.health import router as health_router
from app.api.endpoints.interests import router as interests_router
from app.api.endpoints.topics import router as topics_router
from app.api.endpoints.users import router as users_router
from app.api.endpoints.user_relationships import router as user_relationships_router

# [新增] 录音会话、OSS 素材与片段附件接口。
from app.api.endpoints.memories import router as memories_router
from app.api.endpoints.memory_extraction import router as memory_extraction_router
from app.api.endpoints.memory_contributors import router as memory_contributors_router
from app.api.endpoints.memory_session_members import router as memory_session_members_router

api_router = APIRouter()
# 注册认证接口。
api_router.include_router(auth_router)
api_router.include_router(health_router)
api_router.include_router(interests_router)
# [新增] 首页推荐话题路由。
api_router.include_router(topics_router)
api_router.include_router(users_router)
api_router.include_router(user_relationships_router)
# [新增] 注册录音与素材管理路由。
api_router.include_router(memories_router)
api_router.include_router(memory_extraction_router)
api_router.include_router(memory_session_members_router)
api_router.include_router(memory_contributors_router)
