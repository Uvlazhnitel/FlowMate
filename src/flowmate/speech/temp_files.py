import asyncio
import os
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path


class TemporaryAudioFileService:
    def __init__(self, *, max_age_seconds: int = 3600) -> None:
        if max_age_seconds <= 0:
            raise ValueError("temporary audio max age must be positive")
        self.max_age_seconds = max_age_seconds

    def cleanup_orphans(self, directory: Path | None = None) -> int:
        root = directory or Path(tempfile.gettempdir())
        cutoff = time.time() - self.max_age_seconds
        removed = 0
        for path in root.glob("flowmate-*.ogg"):
            try:
                info = path.lstat()
                if (
                    path.is_symlink()
                    or info.st_uid != os.getuid()
                    or info.st_mtime > cutoff
                ):
                    continue
                path.unlink(missing_ok=True)
                removed += 1
            except FileNotFoundError:
                continue
        return removed

    @asynccontextmanager
    async def create(self) -> AsyncIterator[Path]:
        await asyncio.to_thread(self.cleanup_orphans)
        descriptor, raw_path = tempfile.mkstemp(prefix="flowmate-", suffix=".ogg")
        os.close(descriptor)
        path = Path(raw_path)
        try:
            yield path
        finally:
            await asyncio.to_thread(path.unlink, missing_ok=True)
