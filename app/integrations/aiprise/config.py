from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AiPriseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    aiprise_api_key: str | None = None
    aiprise_webhook_secret: str | None = None
    aiprise_base_url: str = "https://api-sandbox.aiprise.com/api/v1/verify"
    aiprise_environment: str = "sandbox"
    aiprise_timeout_seconds: float = Field(default=20.0, gt=0, le=90)
    aiprise_kyc_template_id: str | None = None
    aiprise_email_check_template_id: str | None = None
    aiprise_phone_check_template_id: str | None = None
    aiprise_kyb_template_id: str | None = None
    aiprise_callback_base_url: str | None = None
    aiprise_webhook_max_body_bytes: int = Field(default=1048576, ge=1024, le=5242880)


aiprise_settings = AiPriseSettings()
