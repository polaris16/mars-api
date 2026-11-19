"""从环境变量和项目根目录的 .env 读取数据库配置。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

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

    # [新增] 开发默认使用本地 Mock 存储；部署时明确切换为 s3。
    object_storage_mode: Literal["mock", "s3"] = "mock"
    object_storage_mock_directory: str = ".local-media"
    object_storage_mock_secret: SecretStr = SecretStr("mars-local-mock-storage-only")
    object_storage_mock_public_base_url: str | None = None
    object_storage_mock_cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost,capacitor://localhost,ionic://localhost"
    )

    # [新增] S3 兼容 OSS 配置；凭据仅由环境变量/.env 注入。
    object_storage_provider: str = "s3"
    object_storage_endpoint_url: str | None = None
    object_storage_region: str = "us-east-1"
    object_storage_bucket: str = ""
    object_storage_access_key_id: str = ""
    object_storage_secret_access_key: SecretStr = SecretStr("")
    object_storage_upload_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    object_storage_read_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    object_storage_max_audio_bytes: int = Field(default=104857600, gt=0)
    object_storage_max_image_bytes: int = Field(default=20971520, gt=0)
    object_storage_max_video_bytes: int = Field(default=524288000, gt=0)
    object_storage_max_text_bytes: int = Field(default=5242880, gt=0)

    # Only the text model is used by Stage 2. The other model groups remain examples.
    ai_text_enabled: bool = False
    ai_text_provider: str = "mock"
    ai_text_base_url: str = ""
    ai_text_api_key: SecretStr = SecretStr("")
    ai_text_model: str = ""
    ai_text_sync_wait_seconds: float = Field(default=5, gt=0, le=30)
    ai_text_timeout_seconds: float = Field(default=30, gt=0, le=180)
    ai_text_max_input_chars: int = Field(default=20000, ge=1, le=500000)
    ai_text_max_attempts: int = Field(default=3, ge=1, le=10)

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
