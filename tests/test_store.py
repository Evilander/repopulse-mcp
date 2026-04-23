from __future__ import annotations

from pathlib import Path

import pytest

from repopulse.retrieval.store import FTSQueryError, Store


def test_fts_search_raises_explicit_error_on_broken_index(tmp_path: Path) -> None:
    store = Store(tmp_path / "index.db")
    try:
        store.raw_conn().execute("DROP TABLE chunks_fts")
        with pytest.raises(FTSQueryError, match="FTS search failed"):
            store.fts_search("token bucket")
    finally:
        store.close()
