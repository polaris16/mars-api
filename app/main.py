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

from app.api.router import api_router
from app.db.database import engine
import uvicorn

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