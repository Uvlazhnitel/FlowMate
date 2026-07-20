from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    app_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("APP_API_KEY", "FLOWMATE_API_BEARER_TOKEN"),
    )
    database_url: str = Field(
        default="postgresql+asyncpg://flowmate:flowmate@localhost:5432/flowmate",
        validation_alias=AliasChoices("DATABASE_URL", "FLOWMATE_DATABASE_URL"),
    )
    telegram_bot_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TELEGRAM_BOT_TOKEN", "FLOWMATE_TELEGRAM_BOT_TOKEN"
        ),
    )
    telegram_allowed_user_ids: Annotated[tuple[int, ...], NoDecode] = Field(
        default=(),
        validation_alias=AliasChoices(
            "TELEGRAM_ALLOWED_USER_IDS", "FLOWMATE_TELEGRAM_ALLOWED_USER_IDS"
        ),
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
            return ()
        if isinstance(value, str):
            try:
                parsed = tuple(int(item.strip()) for item in value.split(","))
            except ValueError as error:
                message = "allowed user IDs must be comma-separated integers"
                raise ValueError(message) from error
            return parsed
        return value

    @field_validator("telegram_allowed_user_ids")
    @classmethod
    def validate_allowed_user_ids(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(user_id <= 0 for user_id in value):
            raise ValueError("allowed user IDs must be positive")
        if len(value) != len(set(value)):
            raise ValueError("allowed user IDs must be unique")
        return value

    def require_api(self) -> Self:
        if not self.app_api_key:
            raise ValueError("APP_API_KEY is required for the API")
        return self

    def require_bot(self) -> Self:
        if not self.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required for the bot")
        if not self.telegram_allowed_user_ids:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS is required for the bot")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
