from __future__ import annotations

import secrets
import warnings
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://ticketing:change-me@localhost:5432/it_ticketing"

    # Auth
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24
    COOKIE_SECURE: bool = False  # Set to True in production (HTTPS)
    ADMIN_DEFAULT_PASSWORD: str = ""  # Override default admin password
    RATE_LIMIT_LOGIN_PER_MINUTE: int = 10

    # SMTP (Notifications)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    NOTIFICATION_FROM_EMAIL: str = "noreply@example.com"

    # Webhook (Notifications)
    WEBHOOK_URLS: str = ""  # comma-separated list

    # Attachments
    UPLOAD_DIR: str = "app/static/uploads"
    MAX_UPLOAD_SIZE_MB: int = 10
    ALLOWED_UPLOAD_EXTENSIONS: str = ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.png,.jpg,.jpeg,.gif,.zip"

    # SLA Hours (per priority)
    SLA_HOURS_URGENT: int = 4
    SLA_HOURS_HIGH: int = 8
    SLA_HOURS_MEDIUM: int = 24
    SLA_HOURS_LOW: int = 48

    # App
    APP_PORT: int = 8000
    WORKERS: int = 2
    DEBUG: bool = False

    @field_validator("JWT_SECRET_KEY", mode="after")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        if not v or v == "change-me-to-a-random-64-char-string-in-production":
            generated = secrets.token_urlsafe(48)
            warnings.warn(
                "JWT_SECRET_KEY not set in environment. "
                f"Generated a random key for this session: {generated[:8]}... "
                "Set JWT_SECRET_KEY in .env for persistence across restarts.",
                RuntimeWarning,
            )
            return generated
        if len(v) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters for security")
        return v

    @field_validator("ADMIN_DEFAULT_PASSWORD", mode="after")
    @classmethod
    def validate_admin_password(cls, v: str) -> str:
        if not v:
            generated = secrets.token_urlsafe(12)
            warnings.warn(
                "ADMIN_DEFAULT_PASSWORD not set. "
                f"Using random password: {generated}. "
                "Set ADMIN_DEFAULT_PASSWORD in .env to control the default admin password.",
                RuntimeWarning,
            )
            return generated
        return v

    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()
