from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from repopulse.retrieval.store import Store
from repopulse.trace import store as trace_store_mod


def test_store_initializes_schema_version(tmp_path: Path) -> None:
    db_path = tmp_path / "index.db"
    store = Store(db_path)
    try:
        row = store.raw_conn().execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
    finally:
        store.close()
    assert row is not None
    assert row["value"] == "1"


def test_store_rejects_future_schema_version(tmp_path: Path) -> None:
    db_path = tmp_path / "index.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', '999')"
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="newer than supported"):
        Store(db_path)


def test_mark_trace_bad_unknown_id_returns_false(tmp_path: Path) -> None:
    store = Store(tmp_path / "index.db")
    try:
        assert store.traces.mark_trace_bad("rp_missing", "nope") is False
    finally:
        store.close()


def test_trace_store_clips_and_prunes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trace_store_mod, "_MAX_TRACE_ROWS", 3)
    store = Store(tmp_path / "index.db")
    try:
        for idx in range(4):
            store.traces.insert_trace(
                trace_id=f"rp_{idx}",
                query="q" * 9000,
                tool_name="search_code",
                params={},
                duration_ms=1,
                result_count=0,
                sufficiency_score=0.0,
                notes={},
            )
        traces = store.traces.list_traces(limit=10)
        assert len(traces) == 3
        assert all(len(trace["query"]) <= 8192 for trace in traces)
        latest = traces[0]["id"]
        assert store.traces.mark_trace_bad(latest, "r" * 2000) is True
        record = store.traces.get_trace(latest)
        assert record is not None
        reason = record["trace"]["notes"]["feedback"]["reason"]
        assert len(reason) <= 1024
    finally:
        store.close()


def test_get_trace_handles_malformed_notes_json(tmp_path: Path) -> None:
    store = Store(tmp_path / "index.db")
    try:
        store.raw_conn().execute(
            """
            INSERT INTO traces(id, created_at, query, tool_name, params_json, duration_ms,
                               result_count, sufficiency_score, notes_json)
            VALUES ('rp_bad', 1, 'q', 'search_code', '{}', 0, 0, 0.0, '{')
            """
        )
        record = store.traces.get_trace("rp_bad")
    finally:
        store.close()
    assert record is not None
    assert record["trace"]["notes"] == {}
