"""健康检查接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.schemas.health import HealthResponse
from app.services.health_service import HealthService

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthResponse, summary="健康检查")
def get_health(
    service: Annotated[HealthService, Depends(HealthService)],
) -> HealthResponse:
    return HealthResponse(status=service.get_status())
