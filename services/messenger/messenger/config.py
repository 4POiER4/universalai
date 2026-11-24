from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MESSENGER_",
        case_sensitive=False,
        extra="ignore",
        enable_decoding=False,
    )

    app_env: str = "development"
    database_url: str = "postgresql://postgres:postgres@postgres:5432/universalai"
    migration_database_url: str = ""
    redis_url: str = "redis://redis:6379/0"
    jwt_public_key: str = ""
    jwt_issuer: str = "universalai"
    jwt_audience: str = "messenger"
    jwt_clock_skew_seconds: int = Field(default=15, ge=0, le=120)
    allowed_origins: tuple[str, ...] = ("http://localhost:5000",)
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "messenger-api")
    message_max_chars: int = Field(default=8000, ge=1, le=32000)
    group_max_members: int = Field(default=250, ge=2, le=5000)
    history_page_size: int = Field(default=200, ge=1, le=500)
    rate_messages_per_minute: int = Field(default=60, ge=1, le=1000)
    rate_connections_per_user: int = Field(default=5, ge=1, le=50)
    ready_require_redis: bool = True
    message_retention_days: int = Field(default=365, ge=1, le=3650)
    audit_retention_days: int = Field(default=365, ge=30, le=3650)
    attachment_root: str = "/attachments"
    attachment_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    attachment_orphan_hours: int = Field(default=24, ge=1, le=168)
    presence_conn_ttl_seconds: int = Field(default=90, ge=30, le=600)
    presence_sweep_interval_seconds: int = Field(default=20, ge=5, le=120)
    presence_manual_sweep_interval_seconds: int = Field(default=60, ge=15, le=600)
    call_ring_timeout_seconds: int = Field(default=45, ge=10, le=120)
    call_max_duration_seconds: int = Field(default=6 * 60 * 60, ge=300, le=24 * 60 * 60)
    call_signal_rate_limit: int = Field(default=120, ge=10, le=1000)
    call_signal_rate_window_seconds: int = Field(default=10, ge=1, le=60)
    livekit_url: str = ""
    livekit_internal_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    group_call_token_ttl_seconds: int = Field(default=21600, ge=300, le=86400)
    group_call_reconcile_interval_seconds: int = Field(default=30, ge=10, le=300)
    clamav_host: str = "clamav"
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    clamav_timeout_seconds: int = Field(default=30, ge=1, le=120)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, value):
        if isinstance(value, str):
            return tuple(item.strip().rstrip("/") for item in value.split(",") if item.strip())
        return tuple(value)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def parse_hosts(cls, value):
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return tuple(value)

    @field_validator("jwt_public_key", mode="before")
    @classmethod
    def restore_pem_newlines(cls, value: str) -> str:
        return (value or "").replace("\\n", "\n").strip()

    def validate_runtime(self) -> None:
        if self.app_env.lower() == "production":
            if not self.jwt_public_key:
                raise ValueError("MESSENGER_JWT_PUBLIC_KEY is required in production")
            if any(origin.startswith("http://") for origin in self.allowed_origins):
                raise ValueError("Only HTTPS origins are allowed in production")
            if not (self.livekit_url and self.livekit_api_key and self.livekit_api_secret):
                raise ValueError(
                    "MESSENGER_LIVEKIT_URL, MESSENGER_LIVEKIT_API_KEY and "
                    "MESSENGER_LIVEKIT_API_SECRET are required in production"
                )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime()
    return settings
