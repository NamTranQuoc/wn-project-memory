from src.core.config import get_settings


def sanitize_and_truncate(
    text: str | None, max_len: int | None = None, read_with: str | None = "get_raw_context"
) -> str:
    """Truncate text for context-window protection. Spec default: 1500 chars.

    `read_with` names the tool that returns the text in full; None when no tool does.
    """
    if text is None:
        return ""
    limit = max_len if max_len is not None else get_settings().sanitize_max_len
    if len(text) <= limit:
        return text
    suffix = f"... [truncated, use {read_with} to read full]" if read_with else "... [truncated]"
    keep = max(0, limit - len(suffix))
    return text[:keep] + suffix
