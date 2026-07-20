import pytest
from pydantic import ValidationError

from flowmate.config import Settings


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

    with pytest.raises(ValueError, match="API_BEARER_TOKEN"):
        settings.require_api()
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        settings.require_bot()
