"""Trace persistence and schema version checks."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterable
from typing import Any

from repopulse.logging import get_logger

SCHEMA_VERSION = 1
Migration = Callable[[sqlite3.Connection], None]
log = get_logger("trace")
_MAX_TRACE_QUERY_CHARS = 8 * 1024
_MAX_TRACE_REASON_CHARS = 1024
_MAX_TRACE_ROWS = 2000


def _migrate_to_v1(_conn: sqlite3.Connection) -> None:
    """Initial traced-retrieval schema.

    Tables are created before migration dispatch, so the v1 step only exists to
    make the version runner explicit and future-proof.
    """


MIGRATIONS: dict[int, Migration] = {
    1: _migrate_to_v1,
}


class TraceStore:
    """Trace-specific access layer over the shared RepoPulse SQLite DB."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._ensure_meta_table()
        self._ensure_trace_schema()
        self._ensure_schema_version()

    def _ensure_meta_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
              key   TEXT PRIMARY KEY,
              value TEXT
            )
            """
        )

    def _ensure_trace_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS traces (
              id                 TEXT PRIMARY KEY,
              created_at         INTEGER NOT NULL,
              query              TEXT NOT NULL,
              tool_name          TEXT NOT NULL,
              params_json        TEXT NOT NULL DEFAULT '{}',
              duration_ms        INTEGER NOT NULL DEFAULT 0,
              result_count       INTEGER NOT NULL DEFAULT 0,
              sufficiency_score  REAL NOT NULL DEFAULT 0,
              notes_json         TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_traces_created ON traces(created_at DESC);

            CREATE TABLE IF NOT EXISTS trace_items (
              trace_id          TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
              rank              INTEGER NOT NULL,
              chunk_id          INTEGER,
              score             REAL NOT NULL,
              components_json   TEXT NOT NULL DEFAULT '{}',
              returned          INTEGER NOT NULL DEFAULT 1,
              PRIMARY KEY (trace_id, rank)
            );
            CREATE INDEX IF NOT EXISTS idx_trace_items_chunk ON trace_items(chunk_id);
            """
        )

    def _ensure_schema_version(self) -> None:
        current = self._current_schema_version()
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"RepoPulse DB schema_version={current} is newer than supported {SCHEMA_VERSION}"
            )
        while current < SCHEMA_VERSION:
            next_version = current + 1
            migration = MIGRATIONS.get(next_version)
            if migration is None:
                raise RuntimeError(
                    f"Missing migration for RepoPulse schema_version {current} -> {next_version}"
                )
            migration(self._conn)
            self._set_schema_version(next_version)
            current = next_version

    def _current_schema_version(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None or row["value"] in {None, ""}:
            return 0
        raw = row["value"]
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid RepoPulse schema_version value: {raw!r}") from exc

    def _set_schema_version(self, version: int) -> None:
        self._conn.execute(
            """
            INSERT INTO meta(key, value) VALUES ('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(version),),
        )

    def insert_trace(
        self,
        *,
        trace_id: str,
        query: str,
        tool_name: str,
        params: dict[str, Any],
        duration_ms: int,
        result_count: int,
        sufficiency_score: float,
        notes: dict[str, Any] | None = None,
    ) -> None:
        safe_query = _clip_text(query, _MAX_TRACE_QUERY_CHARS)
        self._conn.execute(
            """
            INSERT INTO traces(id, created_at, query, tool_name, params_json,
                               duration_ms, result_count, sufficiency_score, notes_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                int(time.time()),
                safe_query,
                tool_name,
                json.dumps(params, default=str),
                duration_ms,
                result_count,
                sufficiency_score,
                json.dumps(notes or {}, default=str),
            ),
        )
        self._prune_old_traces(_MAX_TRACE_ROWS)

    def insert_trace_items(
        self,
        trace_id: str,
        items: Iterable[tuple[int, int | None, float, dict[str, Any], bool]],
    ) -> None:
        self._conn.executemany(
            """
            INSERT INTO trace_items(trace_id, rank, chunk_id, score, components_json, returned)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    trace_id,
                    rank,
                    chunk_id,
                    score,
                    json.dumps(components, default=str),
                    1 if returned else 0,
                )
                for rank, chunk_id, score, components, returned in items
            ],
        )

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM traces WHERE id = ?", (trace_id,)
        ).fetchone()
        if not row:
            return None
        items = self._conn.execute(
            """
            SELECT ti.rank, ti.chunk_id, ti.score, ti.components_json, ti.returned,
                   c.symbol_path, c.symbol_kind, c.start_line, c.end_line, c.breadcrumb,
                   f.path AS path, f.language AS language
            FROM trace_items ti
            LEFT JOIN chunks c ON c.id = ti.chunk_id
            LEFT JOIN files f  ON f.id = c.file_id
            WHERE ti.trace_id = ?
            ORDER BY ti.rank ASC
            """,
            (trace_id,),
        ).fetchall()
        trace = dict(row)
        trace["params"] = _load_json_object(trace.get("params_json"))
        trace["notes"] = _load_json_object(trace.get("notes_json"))
        parsed_items: list[dict[str, Any]] = []
        for item in items:
            payload = dict(item)
            payload["components"] = _load_json_object(payload.get("components_json"))
            parsed_items.append(payload)
        return {
            "trace": trace,
            "items": parsed_items,
            "stages": trace["notes"].get("stages", []),
        }

    def mark_trace_bad(self, trace_id: str, reason: str | None = None) -> bool:
        row = self._conn.execute(
            "SELECT notes_json FROM traces WHERE id = ?", (trace_id,)
        ).fetchone()
        if not row:
            return False
        notes = _load_json_object(row["notes_json"])
        notes["feedback"] = {
            "verdict": "bad",
            "reason": _clip_text(reason, _MAX_TRACE_REASON_CHARS),
        }
        self._conn.execute(
            "UPDATE traces SET notes_json = ? WHERE id = ?",
            (json.dumps(notes, default=str), trace_id),
        )
        return True

    def list_bad_traces(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT id, created_at, query, tool_name, result_count,
                   sufficiency_score, notes_json
            FROM traces
            WHERE json_extract(notes_json, '$.feedback.verdict') = 'bad'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["notes"] = _load_json_object(payload.get("notes_json"))
            results.append(payload)
        return results

    def latest_trace_id(self) -> str | None:
        row = self._conn.execute(
            "SELECT id FROM traces ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return str(row["id"]) if row else None

    def list_traces(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, created_at, query, tool_name, result_count, sufficiency_score "
            "FROM traces ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _prune_old_traces(self, keep: int) -> None:
        self._conn.execute(
            """
            DELETE FROM traces
            WHERE id IN (
              SELECT id FROM traces
              ORDER BY created_at DESC
              LIMIT -1 OFFSET ?
            )
            """,
            (keep,),
        )


def _load_json_object(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        log.warning("trace_json_decode_failed", error=str(exc))
        return {}
    return value if isinstance(value, dict) else {}


def _clip_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
