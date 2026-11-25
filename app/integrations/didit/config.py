from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class DiditSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), env_file_encoding="utf-8", extra="ignore", case_sensitive=False)
    didit_api_key: str | None = None
    redis_url: str | None = None
    didit_webhook_secret: str | None = None
    didit_verification_webhook_secret: str | None = None
    didit_base_url: str = "https://verification.didit.me"
    didit_kyc_workflow_id: str | None = None
    didit_kyb_workflow_id: str | None = None
    didit_timeout_seconds: float = Field(default=12.0, gt=0, le=60)
    didit_sdk_token_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    didit_sdk_token_max_per_minute: int = Field(default=5, ge=1, le=60)
    didit_webhook_tolerance_seconds: int = Field(default=300, ge=30, le=900)
    didit_webhook_max_body_bytes: int = Field(default=262144, ge=1024, le=1048576)

didit_settings = DiditSettings()
