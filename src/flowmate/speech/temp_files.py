import asyncio
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path


class TemporaryAudioFileService:
    @asynccontextmanager
    async def create(self) -> AsyncIterator[Path]:
        descriptor, raw_path = tempfile.mkstemp(prefix="flowmate-", suffix=".ogg")
        os.close(descriptor)
        path = Path(raw_path)
        try:
            yield path
        finally:
            await asyncio.to_thread(path.unlink, missing_ok=True)
