from functools import lru_cache
from typing import Literal
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), env_file_encoding="utf-8", extra="ignore")
    app_name: str = "Everpay API"
    app_env: Literal["local", "test", "staging", "production"] = "local"
    api_v1_prefix: str = "/v1"
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=list)
    database_url: str | None = None
    database_pool_size: int = 10
    database_max_overflow: int = 10
    database_pool_timeout_seconds: int = 10
    supabase_url: str | None = None
    supabase_anon_key: str | None = None
    supabase_service_role_key: str | None = None
    auth_timeout_seconds: float = 5.0
    redis_url: str | None = None
    celery_task_always_eager: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value):
        if not value:
            return value
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql+psycopg://"):
            return value.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
        return value

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
