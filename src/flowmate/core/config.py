from __future__ import annotations

from datetime import time
from functools import lru_cache
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url

PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "ci-test",
    "flowmate",
    "replace-me",
    "test-secret",
)


def is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return any(marker in normalized for marker in PLACEHOLDER_MARKERS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: Literal["development", "test", "production"] = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV", "FLOWMATE_ENVIRONMENT"),
    )
    app_debug: bool = Field(
        default=False,
        validation_alias=AliasChoices("APP_DEBUG", "FLOWMATE_API_DOCS_ENABLED"),
    )
    app_host: str = Field(default="0.0.0.0", validation_alias="APP_HOST")
    app_port: int = Field(default=8000, validation_alias="APP_PORT")
    app_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("APP_API_KEY", "FLOWMATE_API_BEARER_TOKEN"),
    )
    pwa_telegram_user_id: int | None = Field(
        default=None,
        gt=0,
        validation_alias="PWA_TELEGRAM_USER_ID",
    )
    pwa_auth_secret: SecretStr | None = Field(
        default=None,
        validation_alias="PWA_AUTH_SECRET",
    )
    pwa_public_origin: str = Field(
        default="http://localhost:8080",
        validation_alias="PWA_PUBLIC_ORIGIN",
    )
    pwa_cookie_secure: bool = Field(
        default=False,
        validation_alias="PWA_COOKIE_SECURE",
    )
    pwa_login_code_ttl_seconds: int = Field(
        default=600,
        ge=60,
        le=3600,
        validation_alias="PWA_LOGIN_CODE_TTL_SECONDS",
    )
    pwa_login_max_attempts: int = Field(
        default=5,
        ge=1,
        le=20,
        validation_alias="PWA_LOGIN_MAX_ATTEMPTS",
    )
    pwa_login_request_limit: int = Field(
        default=3,
        ge=1,
        le=20,
        validation_alias="PWA_LOGIN_REQUEST_LIMIT",
    )
    pwa_login_request_window_seconds: int = Field(
        default=900,
        ge=60,
        le=86400,
        validation_alias="PWA_LOGIN_REQUEST_WINDOW_SECONDS",
    )
    pwa_session_ttl_days: int = Field(
        default=30,
        ge=1,
        le=365,
        validation_alias="PWA_SESSION_TTL_DAYS",
    )
    pwa_max_active_sessions: int = Field(
        default=5,
        ge=1,
        le=20,
        validation_alias="PWA_MAX_ACTIVE_SESSIONS",
    )
    database_url: str = Field(
        default="postgresql+asyncpg://flowmate:flowmate@localhost:5432/flowmate",
        validation_alias=AliasChoices("DATABASE_URL", "FLOWMATE_DATABASE_URL"),
        repr=False,
    )
    telegram_bot_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TELEGRAM_BOT_TOKEN", "FLOWMATE_TELEGRAM_BOT_TOKEN"
        ),
    )
    telegram_allowed_user_ids: Annotated[frozenset[int], NoDecode] = Field(
        default_factory=frozenset,
        validation_alias=AliasChoices(
            "TELEGRAM_ALLOWED_USER_IDS", "FLOWMATE_TELEGRAM_ALLOWED_USER_IDS"
        ),
    )
    speech_provider: Literal["openai"] | None = Field(
        default=None,
        validation_alias="SPEECH_PROVIDER",
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
    )
    speech_model: str | None = Field(
        default=None,
        validation_alias="SPEECH_MODEL",
    )
    speech_language: str = Field(default="ru", validation_alias="SPEECH_LANGUAGE")
    speech_timeout_seconds: int = Field(
        default=60,
        gt=0,
        validation_alias="SPEECH_TIMEOUT_SECONDS",
    )
    speech_max_file_size_bytes: int = Field(
        default=20_000_000,
        gt=0,
        validation_alias="SPEECH_MAX_FILE_SIZE_BYTES",
    )
    ai_provider: Literal["openai"] | None = Field(
        default=None,
        validation_alias="AI_PROVIDER",
    )
    ai_model: str | None = Field(default=None, validation_alias="AI_MODEL")
    ai_timeout_seconds: int = Field(
        default=60,
        gt=0,
        validation_alias="AI_TIMEOUT_SECONDS",
    )
    app_timezone: str = Field(default="UTC", validation_alias="APP_TIMEZONE")
    app_active_workspace: str = Field(
        default="personal",
        validation_alias="APP_ACTIVE_WORKSPACE",
    )
    ai_high_confidence_threshold: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        validation_alias="AI_HIGH_CONFIDENCE_THRESHOLD",
    )
    ai_clarification_confidence_threshold: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        validation_alias="AI_CLARIFICATION_CONFIDENCE_THRESHOLD",
    )
    draft_ttl_hours: int = Field(
        default=24,
        gt=0,
        le=720,
        validation_alias="DRAFT_TTL_HOURS",
    )
    work_item_action_ttl_minutes: int = Field(
        default=30,
        gt=0,
        le=1440,
        validation_alias="WORK_ITEM_ACTION_TTL_MINUTES",
    )
    scheduler_interval_seconds: int = Field(
        default=30,
        gt=0,
        le=3600,
        validation_alias="SCHEDULER_INTERVAL_SECONDS",
    )
    reminder_batch_size: int = Field(
        default=50,
        gt=0,
        le=500,
        validation_alias="REMINDER_BATCH_SIZE",
    )
    reminder_max_attempts: int = Field(
        default=3,
        gt=0,
        le=20,
        validation_alias="REMINDER_MAX_ATTEMPTS",
    )
    reminder_retry_delay_seconds: int = Field(
        default=60,
        gt=0,
        le=86400,
        validation_alias="REMINDER_RETRY_DELAY_SECONDS",
    )
    reminder_processing_timeout_seconds: int = Field(
        default=300,
        gt=0,
        le=86400,
        validation_alias="REMINDER_PROCESSING_TIMEOUT_SECONDS",
    )
    reminder_delivery_timeout_seconds: int = Field(
        default=15,
        gt=0,
        le=300,
        validation_alias="REMINDER_DELIVERY_TIMEOUT_SECONDS",
    )
    deadline_reminder_lead_minutes: int = Field(
        default=0,
        ge=0,
        le=525_600,
        validation_alias="DEADLINE_REMINDER_LEAD_MINUTES",
    )
    default_morning_digest_time: time = Field(
        default=time(9, 0),
        validation_alias="DEFAULT_MORNING_DIGEST_TIME",
    )
    default_evening_digest_time: time = Field(
        default=time(18, 0),
        validation_alias="DEFAULT_EVENING_DIGEST_TIME",
    )
    default_quiet_hours_start: time = Field(
        default=time(22, 0),
        validation_alias="DEFAULT_QUIET_HOURS_START",
    )
    default_quiet_hours_end: time = Field(
        default=time(8, 0),
        validation_alias="DEFAULT_QUIET_HOURS_END",
    )
    default_snooze_minutes: int = Field(
        default=60,
        ge=1,
        le=10_080,
        validation_alias="DEFAULT_SNOOZE_MINUTES",
    )
    cors_origins: Annotated[frozenset[str], NoDecode] = Field(
        default_factory=frozenset,
        validation_alias="CORS_ORIGINS",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        validation_alias=AliasChoices("LOG_LEVEL", "FLOWMATE_LOG_LEVEL"),
    )

    @field_validator(
        "app_api_key",
        "telegram_bot_token",
        "openai_api_key",
        "pwa_auth_secret",
        mode="before",
    )
    @classmethod
    def normalize_empty_secret(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("pwa_telegram_user_id", mode="before")
    @classmethod
    def normalize_empty_pwa_user_id(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("speech_provider", "ai_provider", mode="before")
    @classmethod
    def normalize_empty_provider(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized or None
        return value

    @field_validator("speech_model", "ai_model", mode="before")
    @classmethod
    def normalize_empty_model(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("speech_language")
    @classmethod
    def validate_speech_language(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 2 or not normalized.isalpha():
            raise ValueError("speech language must be an ISO-639-1 code")
        return normalized

    @field_validator("app_timezone")
    @classmethod
    def validate_app_timezone(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("application timezone must not be empty")
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as error:
            raise ValueError(
                "application timezone must be a valid IANA timezone"
            ) from error
        return normalized

    @field_validator(
        "default_morning_digest_time",
        "default_evening_digest_time",
        "default_quiet_hours_start",
        "default_quiet_hours_end",
    )
    @classmethod
    def validate_local_time(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError("notification default times must not include timezone")
        return value.replace(second=0, microsecond=0)

    @field_validator("app_active_workspace")
    @classmethod
    def validate_active_workspace(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("active workspace must not be empty")
        return normalized

    @field_validator("app_port")
    @classmethod
    def validate_app_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("application port must be between 1 and 65535")
        return value

    @field_validator("pwa_public_origin")
    @classmethod
    def validate_pwa_public_origin(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("PWA public origin must be an HTTP(S) origin")
        return normalized

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("database URL must use postgresql+asyncpg")
        return value

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def parse_allowed_user_ids(cls, value: object) -> object:
        if value is None or value == "":
            return frozenset()
        if isinstance(value, str):
            try:
                parsed = tuple(int(item.strip()) for item in value.split(","))
            except ValueError as error:
                message = "allowed user IDs must be comma-separated integers"
                raise ValueError(message) from error
            if len(parsed) != len(set(parsed)):
                raise ValueError("allowed user IDs must be unique")
            return frozenset(parsed)
        if isinstance(value, (list, tuple)) and len(value) != len(set(value)):
            raise ValueError("allowed user IDs must be unique")
        return value

    @field_validator("telegram_allowed_user_ids")
    @classmethod
    def validate_allowed_user_ids(cls, value: frozenset[int]) -> frozenset[int]:
        if any(user_id <= 0 for user_id in value):
            raise ValueError("allowed user IDs must be positive")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if value is None or value == "":
            return frozenset()
        if isinstance(value, str):
            value = tuple(origin.strip() for origin in value.split(","))
        if isinstance(value, (list, tuple, set, frozenset)):
            normalized: set[str] = set()
            for origin in value:
                if not isinstance(origin, str):
                    raise ValueError("CORS origins must be strings")
                if origin == "*":
                    normalized.add(origin)
                    continue
                parsed = urlsplit(origin)
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.netloc
                    or parsed.path not in {"", "/"}
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError("CORS origins must be HTTP(S) origins")
                normalized.add(origin.rstrip("/"))
            return frozenset(normalized)
        return value

    @model_validator(mode="after")
    def validate_production_settings(self) -> Self:
        if (
            self.ai_clarification_confidence_threshold
            >= self.ai_high_confidence_threshold
        ):
            raise ValueError(
                "AI clarification threshold must be lower than high threshold"
            )
        if self.default_quiet_hours_start == self.default_quiet_hours_end:
            raise ValueError("default quiet hours start and end must differ")
        if (
            self.reminder_processing_timeout_seconds
            <= self.reminder_delivery_timeout_seconds
        ):
            raise ValueError("reminder processing timeout must exceed delivery timeout")
        if (
            self.pwa_telegram_user_id is not None
            and self.pwa_telegram_user_id not in self.telegram_allowed_user_ids
        ):
            raise ValueError("PWA Telegram user must be in the Telegram allowlist")
        if "*" in self.cors_origins:
            raise ValueError("wildcard CORS origins are not allowed")
        if self.app_env != "production":
            return self
        if self.app_debug:
            raise ValueError("APP_DEBUG must be false in production")
        if self.app_api_key is None:
            raise ValueError("APP_API_KEY is required in production")
        api_key = self.app_api_key.get_secret_value()
        if len(api_key) < 32 or is_placeholder(api_key):
            raise ValueError("APP_API_KEY is insecure for production")
        database_password = make_url(self.database_url).password
        if not database_password or is_placeholder(database_password):
            raise ValueError("DATABASE_URL password is insecure for production")
        if self.pwa_telegram_user_id is not None or self.pwa_auth_secret is not None:
            if self.pwa_telegram_user_id is None or self.pwa_auth_secret is None:
                raise ValueError("PWA authentication configuration is incomplete")
            pwa_secret = self.pwa_auth_secret.get_secret_value()
            if len(pwa_secret) < 32 or is_placeholder(pwa_secret):
                raise ValueError("PWA_AUTH_SECRET is insecure for production")
            if not self.pwa_cookie_secure:
                raise ValueError("PWA_COOKIE_SECURE must be true in production")
            if not self.pwa_public_origin.startswith("https://"):
                raise ValueError("PWA_PUBLIC_ORIGIN must use HTTPS in production")
        if self.telegram_bot_token is not None and is_placeholder(
            self.telegram_bot_token.get_secret_value()
        ):
            raise ValueError("TELEGRAM_BOT_TOKEN is insecure for production")
        if self.openai_api_key is not None and is_placeholder(
            self.openai_api_key.get_secret_value()
        ):
            raise ValueError("OPENAI_API_KEY is insecure for production")
        return self

    def require_api(self) -> Self:
        if self.app_api_key is None:
            raise ValueError("APP_API_KEY is required for the API")
        return self

    def require_bot(self) -> Self:
        if self.telegram_bot_token is None:
            raise ValueError("TELEGRAM_BOT_TOKEN is required for the bot")
        if not self.telegram_allowed_user_ids:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS is required for the bot")
        return self

    def require_pwa_auth(self) -> Self:
        if self.pwa_telegram_user_id is None:
            raise ValueError("PWA_TELEGRAM_USER_ID is required for PWA authentication")
        if self.pwa_auth_secret is None:
            raise ValueError("PWA_AUTH_SECRET is required for PWA authentication")
        if self.telegram_bot_token is None:
            raise ValueError("TELEGRAM_BOT_TOKEN is required for PWA authentication")
        if self.pwa_telegram_user_id not in self.telegram_allowed_user_ids:
            raise ValueError("PWA Telegram user must be in the Telegram allowlist")
        return self

    def require_scheduler(self) -> Self:
        if self.telegram_bot_token is None:
            raise ValueError("TELEGRAM_BOT_TOKEN is required for the scheduler")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
