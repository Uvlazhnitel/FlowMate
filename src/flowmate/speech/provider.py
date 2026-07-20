from pathlib import Path
from typing import Protocol


class SpeechToTextProvider(Protocol):
    async def transcribe(self, audio_path: Path) -> str: ...

    async def close(self) -> None: ...
