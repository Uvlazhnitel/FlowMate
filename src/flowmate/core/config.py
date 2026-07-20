from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

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
    cors_origins: Annotated[frozenset[str], NoDecode] = Field(
        default_factory=frozenset,
        validation_alias="CORS_ORIGINS",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        validation_alias=AliasChoices("LOG_LEVEL", "FLOWMATE_LOG_LEVEL"),
    )

    @field_validator("app_port")
    @classmethod
    def validate_app_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("application port must be between 1 and 65535")
        return value

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
        if "*" in self.cors_origins:
            raise ValueError("wildcard CORS origins are not allowed in production")
        if self.telegram_bot_token is not None and is_placeholder(
            self.telegram_bot_token.get_secret_value()
        ):
            raise ValueError("TELEGRAM_BOT_TOKEN is insecure for production")
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
