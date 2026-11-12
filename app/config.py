from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "Everpay API"
    log_level: str = "INFO"
    api_docs_enabled: bool = False
    redis_url: str
    database_url: SecretStr
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore", hide_input_in_errors=True)

@lru_cache
def get_settings() -> Settings:
    return Settings()
