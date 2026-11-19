import sys
import asyncio

# ===== Windows 异步子进程支持 =====
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    # 强制创建一个新循环并设为当前线程的事件循环
    # 这会覆盖可能在策略设置前就被创建的循环
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # 可选：打印验证
    print(f"[Main] Event loop type: {type(loop).__name__}")

"""应用创建、路由注册与资源释放。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.db.database import engine
from app.core.config import get_settings
import uvicorn

@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        engine.dispose()


def create_app() -> FastAPI:
    application = FastAPI(title="Mars API", version="0.1.0", lifespan=lifespan)
    settings = get_settings()
    # [新增] Mock 直传供本地 Web 前端使用；生产 S3 模式不自动开放开发源站。
    if settings.object_storage_mode == "mock":
        origins = [origin.strip() for origin in settings.object_storage_mock_cors_origins.split(",") if origin.strip()]
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            # Web clients also call the authenticated JSON API before and after direct uploads.
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "If-None-Match", "Range"],
            expose_headers=["Content-Length", "Content-Range", "Accept-Ranges"],
        )
    application.include_router(api_router, prefix="/api")
    return application


app = create_app()

# 本地调试启动配置
if __name__ == "__main__":
    # 如果 host 使用 0.0.0.0，则即使有局域网测试需求也可以访问
    # reload=True 在代码修改时会自动重启，非常适合开发阶段
    uvicorn.run(
        "app.main:app",
        host = "127.0.0.1",
        port = 5200,
        reload = True,
        loop="asyncio"    # ← 添加这一行，uvicorn 会调用 asyncio.get_event_loop_policy().new_event_loop() 来创建循环，从而使用之前已经设置的 WindowsProactorEventLoopPolicy
    )
