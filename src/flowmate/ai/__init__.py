from typing import TYPE_CHECKING

from flowmate.ai.provider import AIProvider
from flowmate.ai.schemas import (
    DraftAnalysisResult,
    DraftItem,
    DraftItemType,
    DraftParseResult,
    DraftReadiness,
    DraftSource,
)

if TYPE_CHECKING:
    from flowmate.ai.service import DraftParsingService

__all__ = [
    "AIProvider",
    "DraftAnalysisResult",
    "DraftItem",
    "DraftItemType",
    "DraftParseResult",
    "DraftParsingService",
    "DraftReadiness",
    "DraftSource",
]


def __getattr__(name: str) -> object:
    if name == "DraftParsingService":
        from flowmate.ai.service import DraftParsingService

        return DraftParsingService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
