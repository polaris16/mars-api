"""统一汇总 API 路由。"""

from fastapi import APIRouter

# 手机 App 登录与登出路由。
from app.api.endpoints.auth import router as auth_router
from app.api.endpoints.health import router as health_router
from app.api.endpoints.interests import router as interests_router
from app.api.endpoints.users import router as users_router

api_router = APIRouter()
# 注册认证接口。
api_router.include_router(auth_router)
api_router.include_router(health_router)
api_router.include_router(interests_router)
api_router.include_router(users_router)
