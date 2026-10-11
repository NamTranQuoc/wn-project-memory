"""Pure-code secret redaction: the server-side layer behind the agent rule "never send
secrets to memory".

Two strengths, both deterministic regex (no ML/LLM detection):
  - redact_secrets: raw payloads and embedding input. Also masks `key: value` prose.
  - redact_known_secrets: curated text (facts, notes, recipes, links). Known secret
    formats and explicit `key=value` / `"key": "value"` only, so prose such as
    "token: required for /api" keeps its meaning.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, overload

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

# Key names whose value is a secret (assignments in text, keys in a mapping).
_SECRET_KEY_NAMES = r"""
        (?:api[_-]?key|access[_-]?key|secret[_-]?key|private[_-]?key|
           password|passwd|pwd|token|auth[_-]?token|bearer|
           client[_-]?secret|refresh[_-]?token|session[_-]?key|
           database[_-]?url|db[_-]?password|openai[_-]?api[_-]?key|
           master[_-]?key|connection[_-]?string)"""
# A mapping key such as "password", "jira_api_key" or "github-token".
_SECRET_KEY = re.compile(rf"(?ix)(?:^|[_\-.]){_SECRET_KEY_NAMES}$")

_SECRET_VALUE = r"""
    (                               # group 2: value (masked)
      (?:
        "(?:\\.|[^"\\])*"           # double-quoted
        | '(?:\\.|[^'\\])*'         # single-quoted
        | [^\s,;&}"']+              # bare token
      )
    )"""

# key=value, key: value, "key": "value", key='value', ?token=… in a URL
_SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    (                               # group 1: key + separator (kept)
      (?:^|[\s,;{?&])
      ["']?
      [\w.\-]*?                     # DB_PASSWORD, jira.api_key
      """ + _SECRET_KEY_NAMES + r"""
      ["']?
      \s*[:=]\s*
    )""" + _SECRET_VALUE
)

# As above, but a colon counts only before a quoted value: "token: required" is prose.
_EXPLICIT_ASSIGNMENT = re.compile(
    r"""(?ix)
    (
      (?:^|[\s,;{?&])
      ["']?
      [\w.\-]*?                     # DB_PASSWORD, jira.api_key
      """ + _SECRET_KEY_NAMES + r"""
      ["']?
      (?:\s*=\s*|\s*:\s*(?=["']))
    )""" + _SECRET_VALUE
)

# Authorization: Bearer <token>
_BEARER = re.compile(r"(?i)(\bAuthorization\s*:\s*Bearer\s+)([A-Za-z0-9\-._~+/]+=*)")

# Basic auth header
_BASIC_AUTH = re.compile(r"(?i)(\bAuthorization\s*:\s*Basic\s+)([A-Za-z0-9+/=]+)")


def _redact_formats(text: str) -> str:
    """Secrets recognisable by their shape alone."""
    result = _PEM_PRIVATE_KEY.sub(REDACTION_MASK, text)
    for pattern in _PREFIXED_SECRET_PATTERNS:
        result = pattern.sub(REDACTION_MASK, result)
    result = _URI_PASSWORD.sub(rf"\1:{REDACTION_MASK}@", result)
    result = _BEARER.sub(rf"\1{REDACTION_MASK}", result)
    return _BASIC_AUTH.sub(rf"\1{REDACTION_MASK}", result)


def redact_secrets(text: str | None) -> str:
    """Raw payloads: replace secret-looking spans with ****. Idempotent on masked text."""
    if not text:
        return ""
    return _SECRET_ASSIGNMENT.sub(_mask_assignment, _redact_formats(text))


@overload
def redact_known_secrets(text: str) -> str: ...
@overload
def redact_known_secrets(text: None) -> None: ...
def redact_known_secrets(text: str | None) -> str | None:
    """Curated text: known formats and explicit assignments only. None stays None."""
    if not text:
        return text
    return _EXPLICIT_ASSIGNMENT.sub(_mask_assignment, _redact_formats(text))


def _mask_assignment(match: re.Match[str]) -> str:
    prefix = match.group(1)
    value = match.group(2)
    # Preserve surrounding quotes if present so JSON stays syntactically plausible
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        quote = value[0]
        return f"{prefix}{quote}{REDACTION_MASK}{quote}"
    return f"{prefix}{REDACTION_MASK}"


def redact_mapping(value: Any, redact: Callable[[str], str | None] = redact_secrets) -> Any:
    """Redact a JSON-like value: mask every value under a secret-named key, redact strings."""
    if isinstance(value, dict):
        return {
            key: REDACTION_MASK
            if isinstance(key, str) and _SECRET_KEY.search(key) and value[key] not in (None, "")
            else redact_mapping(value[key], redact)
            for key in value
        }
    if isinstance(value, list):
        return [redact_mapping(item, redact) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value
