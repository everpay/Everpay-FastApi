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
    # Fraud graph (optional; recording is disabled until both are set).
    # FRAUD_GRAPH_HMAC_KEY must equal the Supabase edge-function secret of the same name.
    fraud_graph_hmac_key: SecretStr | None = None
    # Supabase Postgres login that is a member of fraud_graph_writer; sslmode=verify-full and
    # sslrootcert pointing at the Supabase root certificate are required.
    supabase_database_url: SecretStr | None = None
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore", hide_input_in_errors=True)

@lru_cache
def get_settings() -> Settings:
    return Settings()
