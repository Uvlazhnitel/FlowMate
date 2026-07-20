import pytest
from pydantic import ValidationError

from flowmate.core.config import Settings


def test_parses_telegram_allowlist() -> None:
    settings = Settings(
        _env_file=None,
        telegram_allowed_user_ids="123, 456",
    )

    assert settings.telegram_allowed_user_ids == (123, 456)


@pytest.mark.parametrize("value", ["0", "-1", "1,1", "not-an-id"])
def test_rejects_invalid_telegram_allowlist(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            telegram_allowed_user_ids=value,
        )


def test_process_specific_requirements() -> None:
    settings = Settings(_env_file=None)

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
    assert settings.app_api_key == "legacy-secret"
    assert settings.log_level == "WARNING"


@pytest.mark.parametrize("value", [0, 65536])
def test_rejects_invalid_application_port(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_port=value)
