class AIError(Exception):
    """Base error for AI draft parsing."""

    default_safe_code = "ai_error"

    def __init__(self, message: str, *, safe_code: str | None = None) -> None:
        super().__init__(message)
        self.safe_code = safe_code or self.default_safe_code


class AIConfigurationError(AIError):
    """Raised when an enabled AI provider is configured incompletely."""

    default_safe_code = "ai_configuration"


class AIProviderError(AIError):
    """Raised when the configured provider request fails."""

    default_safe_code = "ai_provider"


class AIInvalidResponseError(AIError):
    """Raised when the provider does not return a valid structured draft."""

    default_safe_code = "ai_invalid_response"


class AITimeoutError(AIError):
    """Raised when draft parsing exceeds the configured timeout."""

    default_safe_code = "ai_timeout"


def safe_ai_error_code(error: BaseException) -> str:
    if isinstance(error, AIError):
        return error.safe_code
    return type(error).__name__.lower()[:64]
