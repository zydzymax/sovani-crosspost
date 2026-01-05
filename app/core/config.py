"""
Configuration management for SalesWhisper Crosspost.

This module provides:
- Pydantic Settings for environment variable loading
- Structured configuration classes for different services
- Validation and type safety for configuration values
- Default values and environment-specific overrides
"""

import os
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseSettings):
    """Database configuration settings."""
    model_config = SettingsConfigDict(
        env_prefix="DB_",
        case_sensitive=False,
        extra="ignore"
    )

    database_url: str = Field(
        default="postgresql://saleswhisper:saleswhisper_pass@localhost:5432/saleswhisper_crosspost",
        validation_alias="DATABASE_URL",
        description="PostgreSQL database connection URL"
    )

    # Connection pool settings
    pool_size: int = Field(default=10)
    max_overflow: int = Field(default=20)
    pool_timeout: int = Field(default=30)
    pool_recycle: int = Field(default=3600)

    # Query settings
    statement_timeout: int = Field(default=30)
    echo_sql: bool = Field(default=False)


class RedisConfig(BaseSettings):
    """Redis configuration settings."""
    model_config = SettingsConfigDict(
        env_prefix="REDIS_",
        case_sensitive=False,
        extra="ignore"
    )

    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias="REDIS_URL",
        description="Redis connection URL"
    )

    # Connection settings
    max_connections: int = Field(default=50)
    retry_on_timeout: bool = Field(default=True)
    socket_timeout: float = Field(default=5.0)

    # Celery settings
    celery_broker_db: int = Field(default=0)
    celery_backend_db: int = Field(default=1)


class S3Config(BaseSettings):
    """S3/MinIO storage configuration."""
    model_config = SettingsConfigDict(
        env_prefix="S3_",
        case_sensitive=False,
        extra="ignore"
    )

    endpoint: str = Field(default="http://localhost:9000")
    access_key: str = Field(default="minioadmin")
    secret_key: SecretStr = Field(default="minioadmin123")
    bucket_name: str = Field(default="saleswhisper-media")
    region: str = Field(default="us-east-1")

    # Upload settings
    multipart_threshold: int = Field(default=64 * 1024 * 1024)  # 64MB
    max_concurrency: int = Field(default=10)
    use_ssl: bool = Field(default=False)

    @field_validator('endpoint')
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        if not v.startswith(('http://', 'https://')):
            raise ValueError('S3 endpoint must start with http:// or https://')
        return v


class SecurityConfig(BaseSettings):
    """Security and encryption configuration."""
    model_config = SettingsConfigDict(
        env_prefix="SECURITY_",
        case_sensitive=False,
        extra="ignore"
    )

    # Encryption keys
    aes_key: SecretStr = Field(
        default="01234567890123456789012345678901",
        validation_alias="AES_KEY",
        description="256-bit AES key for data encryption"
    )
    token_encryption_key: SecretStr = Field(
        default="01234567890123456789012345678901",
        validation_alias="TOKEN_ENCRYPTION_KEY"
    )
    jwt_secret_key: SecretStr = Field(
        default="your-jwt-secret-key-here",
        validation_alias="JWT_SECRET_KEY"
    )

    # JWT settings
    jwt_algorithm: str = Field(default="HS256")
    jwt_expiration_hours: int = Field(default=24)

    # API security
    api_key_header: str = Field(default="X-API-Key")
    webhook_secret: SecretStr = Field(default="webhook-secret")

    @field_validator('aes_key')
    @classmethod
    def validate_aes_key(cls, v: SecretStr) -> SecretStr:
        key_str = v.get_secret_value() if isinstance(v, SecretStr) else v
        if len(key_str.encode()) != 32:
            raise ValueError('AES key must be exactly 32 bytes')
        return v


class TelegramConfig(BaseSettings):
    """Telegram bot configuration."""
    model_config = SettingsConfigDict(
        env_prefix="TG_",
        case_sensitive=False,
        extra="ignore"
    )

    bot_token: SecretStr = Field(default="")
    publishing_bot_token: SecretStr = Field(default="")
    admin_channel_id: str = Field(default="")
    webhook_url: str | None = Field(default=None)

    # Bot settings
    webhook_path: str = Field(default="/api/webhooks/telegram")
    max_connections: int = Field(default=100)
    allowed_updates: list[str] = Field(
        default=["message", "channel_post", "edited_message"]
    )


class SocialMediaConfig(BaseSettings):
    """Social media platforms configuration."""
    model_config = SettingsConfigDict(
        env_prefix="SOCIAL_",
        case_sensitive=False,
        extra="ignore"
    )

    # VK
    vk_service_token: SecretStr = Field(default="", validation_alias="VK_SERVICE_TOKEN")
    vk_group_id: str = Field(default="", validation_alias="VK_GROUP_ID")

    # Meta/Instagram
    meta_app_id: str = Field(default="", validation_alias="META_APP_ID")
    meta_app_secret: SecretStr = Field(default="", validation_alias="META_APP_SECRET")
    meta_access_token: SecretStr = Field(default="", validation_alias="META_ACCESS_TOKEN")

    # TikTok
    tiktok_client_key: str = Field(default="", validation_alias="TIKTOK_CLIENT_KEY")
    tiktok_client_secret: SecretStr = Field(default="", validation_alias="TIKTOK_CLIENT_SECRET")
    tiktok_redirect_uri: str = Field(default="", validation_alias="TIKTOK_REDIRECT_URI")

    # YouTube
    youtube_client_id: str = Field(default="", validation_alias="YOUTUBE_CLIENT_ID")
    youtube_client_secret: SecretStr = Field(default="", validation_alias="YOUTUBE_CLIENT_SECRET")
    youtube_redirect_uri: str = Field(default="", validation_alias="YOUTUBE_REDIRECT_URI")

    # Proxy settings for geo-restricted APIs
    ig_proxy_url: str | None = Field(default=None, validation_alias="IG_PROXY_URL")
    tiktok_proxy_url: str | None = Field(default=None, validation_alias="TIKTOK_PROXY_URL")


class MediaConfig(BaseSettings):
    """Media processing configuration."""
    model_config = SettingsConfigDict(
        env_prefix="MEDIA_",
        case_sensitive=False,
        extra="ignore"
    )

    # File limits
    max_file_size_mb: int = Field(default=500, validation_alias="MAX_FILE_SIZE_MB")
    supported_video_formats: list[str] = Field(
        default=["mp4", "mov", "avi", "mkv"]
    )
    supported_image_formats: list[str] = Field(
        default=["jpg", "jpeg", "png", "webp"]
    )

    # FFmpeg settings
    ffmpeg_binary: str = Field(default="ffmpeg")
    ffprobe_binary: str = Field(default="ffprobe")
    max_concurrent_transcodes: int = Field(default=2)

    # Processing timeouts (seconds)
    transcode_timeout: int = Field(default=1800)  # 30 minutes
    analysis_timeout: int = Field(default=60)


class CeleryConfig(BaseSettings):
    """Celery worker configuration."""
    model_config = SettingsConfigDict(
        env_prefix="CELERY_",
        case_sensitive=False,
        extra="ignore"
    )

    # Worker settings
    worker_concurrency: int = Field(default=4)
    worker_max_memory_per_child: int = Field(default=200000)
    worker_max_tasks_per_child: int = Field(default=1000)

    # Task settings
    task_soft_time_limit: int = Field(default=300)
    task_time_limit: int = Field(default=600)
    task_acks_late: bool = Field(default=True)

    # Retry settings
    max_retry_attempts: int = Field(default=3, validation_alias="MAX_RETRY_ATTEMPTS")
    retry_backoff_factor: int = Field(default=2, validation_alias="RETRY_BACKOFF_FACTOR")

    # Queue priorities
    queue_priorities: dict[str, int] = Field(
        default={
            "ingest": 9,
            "enrich": 8,
            "captionize": 7,
            "transcode": 6,
            "preflight": 5,
            "publish": 4,
            "finalize": 3
        }
    )


class AppConfig(BaseSettings):
    """Main application configuration."""
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore"
    )

    # App metadata
    app_name: str = Field(default="SalesWhisper Crosspost", validation_alias="APP_NAME")
    version: str = Field(default="1.0.0", validation_alias="APP_VERSION")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    debug: bool = Field(default=True, validation_alias="DEBUG")

    # API settings
    api_host: str = Field(default="0.0.0.0", validation_alias="API_HOST")
    api_port: int = Field(default=8000, validation_alias="API_PORT")
    api_workers: int = Field(default=1, validation_alias="API_WORKERS")

    # Rate limiting
    api_rate_limit_per_minute: int = Field(default=100, validation_alias="API_RATE_LIMIT_PER_MINUTE")

    # Brand settings
    brand_name: str = Field(default="SalesWhisper", validation_alias="BRAND_NAME")
    brand_handle: str = Field(default="@saleswhisper_official", validation_alias="BRAND_HANDLE")
    brand_timezone: str = Field(default="Europe/Moscow", validation_alias="BRAND_TZ")

    # Logging
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_format: str = Field(default="json", validation_alias="LOG_FORMAT")

    @field_validator('environment')
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = ['development', 'testing', 'staging', 'production']
        if v not in allowed:
            raise ValueError(f'Environment must be one of: {allowed}')
        return v

    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in allowed:
            raise ValueError(f'Log level must be one of: {allowed}')
        return v.upper()

    @property
    def is_development(self) -> bool:
        return self.environment == 'development'

    @property
    def is_production(self) -> bool:
        return self.environment == 'production'


class EmailConfig(BaseSettings):
    """Email/SMTP configuration for notifications and auth codes."""
    model_config = SettingsConfigDict(
        env_prefix="SMTP_",
        case_sensitive=False,
        extra="ignore"
    )

    host: str = Field(default="smtp.yandex.ru", validation_alias="SMTP_HOST")
    port: int = Field(default=465, validation_alias="SMTP_PORT")
    user: str = Field(default="", validation_alias="SMTP_USER")
    password: SecretStr = Field(default="", validation_alias="SMTP_PASSWORD")
    from_email: str = Field(
        default="SalesWhisper <auth@saleswhisper.pro>",
        validation_alias="SMTP_FROM"
    )
    use_ssl: bool = Field(default=True, validation_alias="SMTP_USE_SSL")
    timeout: int = Field(default=30, validation_alias="SMTP_TIMEOUT")

    # Auth code settings
    auth_code_ttl: int = Field(default=300, description="Auth code TTL in seconds")
    auth_code_length: int = Field(default=6, description="Auth code length")


class PaymentConfig(BaseSettings):
    """Payment provider configuration (Tochka Bank)."""
    model_config = SettingsConfigDict(
        env_prefix="PAYMENT_",
        case_sensitive=False,
        extra="ignore"
    )

    provider: str = Field(default="tochka", validation_alias="PAYMENT_PROVIDER")
    merchant_id: str = Field(default="", validation_alias="TOCHKA_MERCHANT_ID")
    secret_key: SecretStr = Field(default="", validation_alias="TOCHKA_SECRET_KEY")
    api_url: str = Field(
        default="https://api.tochka.com",
        validation_alias="TOCHKA_API_URL"
    )
    callback_url: str = Field(
        default="https://crosspost.saleswhisper.pro/api/v1/checkout/webhook/tochka",
        validation_alias="TOCHKA_CALLBACK_URL"
    )
    success_url: str = Field(
        default="https://saleswhisper.pro/payment/success",
        validation_alias="PAYMENT_SUCCESS_URL"
    )
    fail_url: str = Field(
        default="https://saleswhisper.pro/payment/fail",
        validation_alias="PAYMENT_FAIL_URL"
    )


class Settings:
    """Main settings container with all configuration sections."""

    def __init__(self):
        self.app = AppConfig()
        self.database = DatabaseConfig()
        self.redis = RedisConfig()
        self.s3 = S3Config()
        self.security = SecurityConfig()
        self.telegram = TelegramConfig()
        self.social_media = SocialMediaConfig()
        self.media = MediaConfig()
        self.celery = CeleryConfig()
        self.email = EmailConfig()
        self.payment = PaymentConfig()

    def get_database_url(self, async_driver: bool = False) -> str:
        """Get database URL with optional async driver."""
        url = str(self.database.database_url)
        if async_driver:
            url = url.replace("postgresql://", "postgresql+asyncpg://")
        return url

    def get_redis_url(self, db: int | None = None) -> str:
        """Get Redis URL with optional database number."""
        url = str(self.redis.redis_url)
        if db is not None:
            # Replace database number in URL
            url = url.rsplit('/', 1)[0] + f'/{db}'
        return url

    def get_s3_config(self) -> dict[str, Any]:
        """Get S3 configuration as dictionary."""
        return {
            'endpoint_url': self.s3.endpoint,
            'aws_access_key_id': self.s3.access_key,
            'aws_secret_access_key': self.s3.secret_key.get_secret_value(),
            'region_name': self.s3.region,
            'use_ssl': self.s3.use_ssl
        }


# Global settings instance
settings = Settings()


# Example usage and testing helpers
if __name__ == "__main__":
    """Example usage and configuration validation."""

    print("=' SalesWhisper Crosspost Configuration")
    print(f"Environment: {settings.app.environment}")
    print(f"Debug mode: {settings.app.debug}")
    print(f"Database URL: {settings.get_database_url()}")
    print(f"Redis URL: {settings.get_redis_url()}")
    print(f"S3 Endpoint: {settings.s3.endpoint}")

    # Validate all configurations
    try:
        print("\n Configuration validation:")
        print("  App config: OK")
        print("  Database config: OK")
        print("  Redis config: OK")
        print("  S3 config: OK")
        print("  Security config: OK")
        print(f"  Telegram config: {'OK' if settings.telegram.bot_token else 'Missing bot token'}")
        print(f"  Social media config: {'OK' if settings.social_media.vk_service_token else 'Missing tokens'}")
        print("  Media config: OK")
        print("  Celery config: OK")

    except Exception as e:
        print(f"L Configuration error: {e}")


def get_test_settings() -> Settings:
    """Get test-specific settings with overrides."""

    # Override with test values
    os.environ.update({
        'ENVIRONMENT': 'testing',
        'DEBUG': 'true',
        'DATABASE_URL': 'postgresql://test:test@localhost:5432/test_db',
        'REDIS_URL': 'redis://localhost:6379/15',  # Use DB 15 for tests
        'S3_BUCKET_NAME': 'test-bucket'
    })

    return Settings()
