from src.core.config import get_settings

TRUNCATE_SUFFIX = "... [truncated, use get_raw_context to read full]"


def sanitize_and_truncate(text: str | None, max_len: int | None = None) -> str:
    """Truncate text for context-window protection. Spec default: 1500 chars."""
    if text is None:
        return ""
    limit = max_len if max_len is not None else get_settings().sanitize_max_len
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(TRUNCATE_SUFFIX))
    return text[:keep] + TRUNCATE_SUFFIX
