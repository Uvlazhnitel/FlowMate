from typing import Any, cast

import pytest
from pydantic import ValidationError

from flowmate.core.config import Settings, get_settings
from tests.conftest import validate_test_database_url


def production_settings(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "app_env": "production",
        "app_debug": False,
        "app_api_key": "a-strong-production-key-with-32-characters",
        "database_url": (
            "postgresql+asyncpg://flowmate:StrongPassword42@postgres:5432/flowmate"
        ),
    }
    values.update(overrides)
    return values


def test_loads_development_defaults_without_environment() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.app_debug is False
    assert settings.app_host == "0.0.0.0"
    assert settings.app_port == 8000
    assert settings.telegram_allowed_user_ids == frozenset()
    assert settings.speech_provider is None
    assert settings.speech_language == "ru"
    assert settings.speech_timeout_seconds == 60
    assert settings.speech_max_file_size_bytes == 20_000_000


def test_parses_speech_configuration() -> None:
    settings = Settings(
        _env_file=None,
        speech_provider="OPENAI",
        openai_api_key="private-openai-key",
        speech_model=" configured-model ",
        speech_language="EN",
        speech_timeout_seconds=45,
        speech_max_file_size_bytes=1_000_000,
    )

    assert settings.speech_provider == "openai"
    assert settings.speech_model == "configured-model"
    assert settings.speech_language == "en"
    assert settings.speech_timeout_seconds == 45
    assert settings.speech_max_file_size_bytes == 1_000_000


def test_empty_speech_configuration_is_disabled() -> None:
    settings = Settings(
        _env_file=None,
        speech_provider=" ",
        openai_api_key="",
        speech_model=" ",
    )

    assert settings.speech_provider is None
    assert settings.openai_api_key is None
    assert settings.speech_model is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("speech_language", "russian"),
        ("speech_language", "1x"),
        ("speech_timeout_seconds", 0),
        ("speech_max_file_size_bytes", 0),
    ],
)
def test_rejects_invalid_speech_configuration(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **cast(Any, {field: value}))


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+asyncpg://flowmate:password@localhost:5432/flowmate",
        "postgresql://flowmate:password@localhost:5432/flowmate_test",
    ],
)
def test_rejects_unsafe_test_database_url(database_url: str) -> None:
    with pytest.raises(ValueError, match="TEST_DATABASE_URL"):
        validate_test_database_url(database_url)


def test_parses_telegram_allowlist_as_frozenset() -> None:
    settings = Settings(
        _env_file=None,
        telegram_allowed_user_ids="123, 456",
    )

    assert settings.telegram_allowed_user_ids == frozenset({123, 456})


@pytest.mark.parametrize("value", ["0", "-1", "1,1", "not-an-id"])
def test_rejects_invalid_telegram_allowlist(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            telegram_allowed_user_ids=value,
        )


def test_parses_and_normalizes_cors_origins() -> None:
    settings = Settings(
        _env_file=None,
        cors_origins="https://example.com/, http://localhost:3000",
    )

    assert settings.cors_origins == frozenset(
        {"https://example.com", "http://localhost:3000"}
    )


@pytest.mark.parametrize(
    "value", ["example.com", "ftp://example.com", "https://example.com/path"]
)
def test_rejects_invalid_cors_origins(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=value)


def test_process_specific_requirements() -> None:
    settings = Settings(_env_file=None)

    with pytest.raises(ValueError, match="APP_API_KEY"):
        settings.require_api()
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        settings.require_bot()


def test_empty_process_secrets_are_treated_as_missing() -> None:
    settings = Settings(
        _env_file=None,
        app_api_key=" ",
        telegram_bot_token="",
        telegram_allowed_user_ids="123",
    )

    with pytest.raises(ValueError, match="APP_API_KEY"):
        settings.require_api()
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        settings.require_bot()


def test_accepts_legacy_environment_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWMATE_ENVIRONMENT", "test")
    monkeypatch.setenv("FLOWMATE_API_BEARER_TOKEN", "legacy-secret")
    monkeypatch.setenv("FLOWMATE_LOG_LEVEL", "WARNING")

    settings = Settings(_env_file=None)

    assert settings.app_env == "test"
    assert settings.app_api_key is not None
    assert settings.app_api_key.get_secret_value() == "legacy-secret"
    assert settings.log_level == "WARNING"


@pytest.mark.parametrize("value", [0, 65536])
def test_rejects_invalid_application_port(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_port=value)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"app_debug": True}, "APP_DEBUG"),
        ({"app_api_key": "short"}, "APP_API_KEY"),
        ({"app_api_key": "replace-me-with-a-secure-key-xxxxxxxx"}, "APP_API_KEY"),
        (
            {
                "database_url": (
                    "postgresql+asyncpg://flowmate:replace-me@postgres:5432/flowmate"
                )
            },
            "DATABASE_URL",
        ),
        ({"cors_origins": "*"}, "wildcard CORS"),
        ({"telegram_bot_token": "123456:replace-me"}, "TELEGRAM_BOT_TOKEN"),
        ({"openai_api_key": "replace-me"}, "OPENAI_API_KEY"),
    ],
)
def test_rejects_insecure_production_configuration(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(
            _env_file=None,
            **cast(Any, production_settings(**overrides)),
        )


def test_accepts_secure_production_configuration() -> None:
    settings = Settings(_env_file=None, **cast(Any, production_settings()))

    assert settings.app_env == "production"


def test_settings_repr_masks_secrets() -> None:
    settings = Settings(
        _env_file=None,
        app_api_key="private-api-key",
        telegram_bot_token="123456:private-token",
        openai_api_key="private-openai-key",
        database_url=(
            "postgresql+asyncpg://flowmate:private-password@localhost:5432/flowmate"
        ),
    )

    representation = repr(settings)
    assert "private-api-key" not in representation
    assert "private-token" not in representation
    assert "private-openai-key" not in representation
    assert "private-password" not in representation


def test_get_settings_returns_one_cached_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_API_KEY", "cached-test-key")
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
