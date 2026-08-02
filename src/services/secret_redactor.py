"""Pure-code secret redaction before any LLM / embedding outbound call.

No ML/LLM detection — deterministic regex and structural heuristics only.
"""

from __future__ import annotations

import re

REDACTION_MASK = "****"

# High-confidence token / key prefixes (value body masked entirely).
_PREFIXED_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # OpenAI / project keys
    re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bsk-ant-api\d{2}-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    # Slack
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
    # Google API
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b"),
    # AWS access key id
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Stripe
    re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    # JWT (header.payload.signature)
    re.compile(r"\beyJ[A-Za-z0-9_\-]+=*\.[A-Za-z0-9_\-]+=*\.[A-Za-z0-9_\-]+=*\b"),
]

# PEM private key blocks
_PEM_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"[\s\S]*?"
    r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)

# Connection URI user:password@host (incl. SQLAlchemy drivers like postgresql+asyncpg)
_URI_PASSWORD = re.compile(
    r"((?:postgres(?:ql)?(?:\+\w+)?|mysql(?:\+\w+)?|mongodb(?:\+srv)?|"
    r"redis|amqp|https?)://"
    r"[^:\s/]+):([^@\s/]+)@",
    re.IGNORECASE,
)

# Assignment / JSON-ish secret values.
# Captures key=value, key: value, "key": "value", key='value'
_SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    (                               # group 1: key + separator (kept)
      (?:^|[\s,;{])
      ["']?
      (?:
        (?:api[_-]?key|access[_-]?key|secret[_-]?key|private[_-]?key|
           password|passwd|pwd|token|auth[_-]?token|bearer|
           client[_-]?secret|refresh[_-]?token|session[_-]?key|
           database[_-]?url|db[_-]?password|openai[_-]?api[_-]?key|
           master[_-]?key|connection[_-]?string)
      )
      ["']?
      \s*[:=]\s*
    )
    (                               # group 2: value (masked)
      (?:
        "(?:\\.|[^"\\])*"           # double-quoted
        | '(?:\\.|[^'\\])*'         # single-quoted
        | [^\s,;}"']+               # bare token
      )
    )
    """
)

# Authorization: Bearer <token>
_BEARER = re.compile(r"(?i)(\bAuthorization\s*:\s*Bearer\s+)([A-Za-z0-9\-._~+/]+=*)")

# Basic auth header
_BASIC_AUTH = re.compile(r"(?i)(\bAuthorization\s*:\s*Basic\s+)([A-Za-z0-9+/=]+)")


def redact_secrets(text: str | None) -> str:
    """Replace secret-looking spans with ****. Idempotent on already-masked text."""
    if not text:
        return ""

    result = text

    result = _PEM_PRIVATE_KEY.sub(REDACTION_MASK, result)

    for pattern in _PREFIXED_SECRET_PATTERNS:
        result = pattern.sub(REDACTION_MASK, result)

    result = _URI_PASSWORD.sub(rf"\1:{REDACTION_MASK}@", result)
    result = _BEARER.sub(rf"\1{REDACTION_MASK}", result)
    result = _BASIC_AUTH.sub(rf"\1{REDACTION_MASK}", result)
    result = _SECRET_ASSIGNMENT.sub(_mask_assignment, result)

    return result


def _mask_assignment(match: re.Match[str]) -> str:
    prefix = match.group(1)
    value = match.group(2)
    # Preserve surrounding quotes if present so JSON stays syntactically plausible
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        quote = value[0]
        return f"{prefix}{quote}{REDACTION_MASK}{quote}"
    return f"{prefix}{REDACTION_MASK}"
