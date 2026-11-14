"""从环境变量和项目根目录的 .env 读取数据库配置。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = "mars"
    postgres_password: SecretStr = SecretStr("")
    postgres_database: str = "mars"
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout: int = Field(default=30, gt=0)
    db_pool_recycle: int = Field(default=1800, gt=0)
    db_connect_timeout: int = Field(default=10, gt=0)
    db_echo: bool = False

    @property
    def database_url(self) -> URL:
        """使用原始密码构造 URL，避免特殊字符被误解析。"""
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_database,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
