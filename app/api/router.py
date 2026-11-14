"""统一汇总 API 路由。"""

from fastapi import APIRouter

from app.api.endpoints.health import router as health_router
from app.api.endpoints.users import router as users_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(users_router)
