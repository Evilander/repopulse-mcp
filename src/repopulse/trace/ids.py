"""Short, memorable trace ids like `rp_7fa3`."""

from __future__ import annotations

import secrets
import string

_ALPHABET = string.ascii_lowercase + string.digits


def new_id(length: int = 8) -> str:
    # 8 chars of [a-z0-9] = ~41 bits of entropy. Expected first collision at
    # ~1M traces, which is plenty for a local-dev tool.
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(length))
    return f"rp_{suffix}"
