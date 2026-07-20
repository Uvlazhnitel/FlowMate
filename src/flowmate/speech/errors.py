class SpeechError(Exception):
    """Base error for safe speech processing failures."""


class SpeechConfigurationError(SpeechError):
    """Speech transcription configuration is incomplete."""


class SpeechProviderError(SpeechError):
    """The configured speech provider failed."""


class InvalidTranscriptionResponseError(SpeechError):
    """The speech provider returned no usable transcription."""


class AudioTooLargeError(SpeechError):
    """The audio file exceeds the configured size limit."""


class SpeechTimeoutError(SpeechError):
    """Voice download or transcription exceeded its deadline."""
