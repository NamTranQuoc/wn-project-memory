import hashlib
import re


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compute_content_hash(content: str) -> str:
    return sha256_hex(content)


def compute_source_hash(content: str) -> str:
    return sha256_hex(content)


def canonicalize_content(content: str) -> str:
    """Stable text form for content hashing across re-fetches."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).strip()


def build_item_key(*, external_id: str | None, content_hash: str) -> str:
    """Prefer stable external id; fall back to content hash."""
    if external_id is not None:
        cleaned = external_id.strip()
        if cleaned:
            # Collapse internal whitespace so accidental spacing does not fork keys.
            cleaned = re.sub(r"\s+", " ", cleaned)
            return f"ext:{cleaned}"
    return f"hash:{content_hash}"
