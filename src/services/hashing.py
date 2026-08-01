import hashlib


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compute_content_hash(content: str) -> str:
    return sha256_hex(content)


def compute_source_hash(content: str) -> str:
    return sha256_hex(content)
