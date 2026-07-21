class AIError(Exception):
    """Base error for AI draft parsing."""


class AIConfigurationError(AIError):
    """Raised when an enabled AI provider is configured incompletely."""


class AIProviderError(AIError):
    """Raised when the configured provider request fails."""


class AIInvalidResponseError(AIError):
    """Raised when the provider does not return a valid structured draft."""


class AITimeoutError(AIError):
    """Raised when draft parsing exceeds the configured timeout."""
