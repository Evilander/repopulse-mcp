"""Content guards for files that should never be indexed or served."""

from __future__ import annotations

import re

_PRIVATE_KEY_RE = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_AWS_ACCESS_KEY_RE = re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_SECRET_ASSIGNMENT_RE = re.compile(
    rb"(?ix)"
    rb"\b(?:"
    rb"aws_secret_access_key|"
    rb"secret(?:_access_key)?|"
    rb"api[_-]?key|"
    rb"access[_-]?token|"
    rb"token|"
    rb"password|"
    rb"passwd|"
    rb"private[_-]?key"
    rb")\b"
    rb".{0,40}"
    rb"[:=]"
    rb".{0,8}"
    rb"['\"]?[A-Za-z0-9/+_=.-]{20,}['\"]?"
)

_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("private_key", _PRIVATE_KEY_RE),
    ("aws_access_key", _AWS_ACCESS_KEY_RE),
    ("secret_assignment", _SECRET_ASSIGNMENT_RE),
)

_MAX_SCAN_BYTES = 128 * 1024


def detect_sensitive_content(content: bytes | str) -> str | None:
    """Return a short reason if `content` looks like a secret-bearing file."""
    sample = content.encode("utf-8", errors="ignore") if isinstance(content, str) else content
    sample = sample[:_MAX_SCAN_BYTES]
    for reason, pattern in _PATTERNS:
        if pattern.search(sample):
            return reason
    return None
