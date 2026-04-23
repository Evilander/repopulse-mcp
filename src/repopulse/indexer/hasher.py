"""Content hashing — stable chunk/file identity across indexing runs."""

from __future__ import annotations

import hashlib

_DIGEST_BYTES = 16  # 128 bits is plenty for dedup; fits in a TEXT column.


def content_hash(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=_DIGEST_BYTES).hexdigest()
