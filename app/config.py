1|from __future__ import annotations
2|
3|import secrets
4|import warnings
5|from pathlib import Path
6|
7|from pydantic import field_validator
8|from pydantic_settings import BaseSettings
9|
10|
11|class Settings(BaseSettings):
12|    # Database
13|    DATABASE_URL: str = "postgresql+asyncpg://ticketing:change-me@localhost:5432/it_ticketing"
14|
15|    # Auth
16|    JWT_SECRET_KEY: str = ""
17|    JWT_ALGORITHM: str = "HS256"
18|    JWT_EXPIRATION_HOURS: int = 24
19|    COOKIE_SECURE: bool = False  # Set to True in production (HTTPS)
20|    ADMIN_DEFAULT_PASSWORD: str = ""  # Override default admin password
21|    RATE_LIMIT_LOGIN_PER_MINUTE: int = 10
22|
23|    # SMTP (Notifications)
24|    SMTP_HOST: str = ""
25|    SMTP_PORT: int = 587
26|    SMTP_USER: str = ""
27|    SMTP_PASSWORD: str = ""
28|    SMTP_USE_TLS: bool = True
29|    NOTIFICATION_FROM_EMAIL: str = "noreply@example.com"
30|
31|    # Webhook (Notifications)
32|    WEBHOOK_URLS: str = ""  # comma-separated list
33|
34|    # Attachments
35|    UPLOAD_DIR: str = "app/static/uploads"
36|    MAX_UPLOAD_SIZE_MB: int = 10
37|    ALLOWED_UPLOAD_EXTENSIONS: str = ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.png,.jpg,.jpeg,.gif,.zip"
38|
39|    # SLA Hours (per priority)
40|    SLA_HOURS_URGENT: int = 1
41|    SLA_HOURS_HIGH: int = 2
42|    SLA_HOURS_MEDIUM: int = 4
43|    SLA_HOURS_LOW: int = 8
44|
45|    # App
46|    APP_PORT: int = 8000
47|    WORKERS: int = 2
48|    DEBUG: bool = False
49|
50|    @field_validator("JWT_SECRET_KEY", mode="after")
51|    @classmethod
52|    def validate_secret_key(cls, v: str) -> str:
53|        if not v or v == "change-me-to-a-random-64-char-string-in-production":
54|            generated = secrets.token_urlsafe(48)
55|            warnings.warn(
56|                "JWT_SECRET_KEY not set in environment. "
57|                f"Generated a random key for this session: {generated[:8]}... "
58|                "Set JWT_SECRET_KEY in .env for persistence across restarts.",
59|                RuntimeWarning,
60|            )
61|            return generated
62|        if len(v) < 32:
63|            raise ValueError("JWT_SECRET_KEY must be at least 32 characters for security")
64|        return v
65|
66|    @field_validator("ADMIN_DEFAULT_PASSWORD", mode="after")
67|    @classmethod
68|    def validate_admin_password(cls, v: str) -> str:
69|        if not v:
70|            generated = secrets.token_urlsafe(12)
71|            warnings.warn(
72|                "ADMIN_DEFAULT_PASSWORD not set. "
73|                f"Using random password: {generated}. "
74|                "Set ADMIN_DEFAULT_PASSWORD in .env to control the default admin password.",
75|                RuntimeWarning,
76|            )
77|            return generated
78|        return v
79|
80|    class Config:
81|        env_file = ".env"
82|        extra = "allow"
83|
84|
85|settings = Settings()
86|