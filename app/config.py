from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "postgresql://user:password@db:5432/mydatabase"
    redis_url: str = "redis://redis:6377/0"
    cache_ttl_seconds: int = 300 # Cache subscriptions for 5 minutes
    webhook_delivery_timeout_seconds: int = 10
    celery_max_retries: int = 7
    celery_base_retry_delay_seconds: int = 10 # 10s, 30s, 1m30s, 4m30s, 13m30s, 40m30s, 2h+
    log_retention_hours: int = 72 # 3 days
    # Optional: Secret for signing/verifying internal communication or other purposes
    # app_secret_key: str = "changeit"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()