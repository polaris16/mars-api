"""应用创建、路由注册与资源释放。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.db.database import engine


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        engine.dispose()


def create_app() -> FastAPI:
    application = FastAPI(title="Mars API", version="0.1.0", lifespan=lifespan)
    application.include_router(api_router, prefix="/api")
    return application


app = create_app()
