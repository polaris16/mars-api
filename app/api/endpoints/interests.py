"""可选兴趣领域目录接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.interest import InterestResponse
from app.services.interest_service import InterestService

router = APIRouter(prefix="/interests", tags=["interests"])


# App 展示兴趣标签选择项，无需登录。
@router.get("", response_model=list[InterestResponse], summary="获取可选兴趣领域")
def list_interests(
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[InterestService, Depends(InterestService)],
) -> list[InterestResponse]:
    return [service.to_response(item) for item in service.list_available(session)]
