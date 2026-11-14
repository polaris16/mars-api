"""同步 PostgreSQL 引擎、连接池与请求级会话。"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# 创建 Engine 不会连接数据库，首次执行数据库操作时才获取连接。
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=settings.db_pool_recycle,
    connect_args={"connect_timeout": settings.db_connect_timeout},
    echo=settings.db_echo,
    hide_parameters=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """供 Depends 注入；释放会话时会回滚未提交事务，不自动提交。"""
    with SessionLocal() as session:
        yield session
