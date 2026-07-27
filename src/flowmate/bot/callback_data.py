def encode_revision(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value < 0:
        raise ValueError("revision must not be negative")
    if value == 0:
        return "0"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = alphabet[remainder] + result
    return result


def decode_revision(value: str) -> int | None:
    try:
        return int(value, 36)
    except ValueError:
        return None
