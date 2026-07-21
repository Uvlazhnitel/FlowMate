TELEGRAM_TEXT_LIMIT = 4000


def split_plain_text(text: str, max_length: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_length:
        newline = remaining.rfind("\n", 0, max_length + 1)
        space = remaining.rfind(" ", 0, max_length + 1)
        boundary = max(newline, space)
        split_at = boundary + 1 if boundary >= max_length // 2 else max_length
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        chunks.append(remaining)
    return chunks
