from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLOWMATE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: str = "postgresql+asyncpg://flowmate:flowmate@localhost:5432/flowmate"
    api_bearer_token: str | None = None
    telegram_bot_token: str | None = None
    telegram_allowed_user_ids: Annotated[tuple[int, ...], NoDecode] = ()
    api_docs_enabled: bool = True

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
        if not self.api_bearer_token:
            raise ValueError("FLOWMATE_API_BEARER_TOKEN is required for the API")
        return self

    def require_bot(self) -> Self:
        if not self.telegram_bot_token:
            raise ValueError("FLOWMATE_TELEGRAM_BOT_TOKEN is required for the bot")
        if not self.telegram_allowed_user_ids:
            message = "FLOWMATE_TELEGRAM_ALLOWED_USER_IDS is required for the bot"
            raise ValueError(message)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
