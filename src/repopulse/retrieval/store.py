"""SQLite-backed store: files, chunks, FTS5, symbols, references.

sqlite-vec (for dense vectors) is loaded lazily when embeddings are enabled.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Generator, Iterable
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repopulse.logging import get_logger
from repopulse.trace.store import TraceStore

log = get_logger("store")


@dataclass
class FileRow:
    id: int
    path: str
    language: str | None
    size_bytes: int
    mtime_ns: int
    content_hash: str
    indexed_at: int


@dataclass
class ChunkRow:
    id: int
    file_id: int
    symbol_path: str
    symbol_kind: str
    start_line: int
    end_line: int
    byte_start: int
    byte_end: int
    breadcrumb: str
    text: str
    token_estimate: int


class FTSQueryError(RuntimeError):
    """Raised when the FTS query itself or the index state is invalid."""

    def __init__(self, message: str, *, kind: str, query: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.query = query


class Store:
    """Thin SQLite wrapper with prepared schema and parameterized queries."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(
            str(db_path), isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._conn.execute("PRAGMA synchronous = NORMAL;")
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.execute("PRAGMA busy_timeout = 5000;")
        self._init_schema()
        self.traces = TraceStore(self._conn)

    # --- schema ---

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
              key   TEXT PRIMARY KEY,
              value TEXT
            );
            CREATE TABLE IF NOT EXISTS files (
              id            INTEGER PRIMARY KEY,
              path          TEXT UNIQUE NOT NULL,
              language      TEXT,
              size_bytes    INTEGER NOT NULL,
              mtime_ns      INTEGER NOT NULL,
              content_hash  TEXT NOT NULL,
              indexed_at    INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
              id              INTEGER PRIMARY KEY,
              file_id         INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              symbol_path     TEXT NOT NULL DEFAULT '',
              symbol_kind     TEXT NOT NULL DEFAULT '',
              start_line      INTEGER NOT NULL,
              end_line        INTEGER NOT NULL,
              byte_start      INTEGER NOT NULL,
              byte_end        INTEGER NOT NULL,
              breadcrumb      TEXT NOT NULL DEFAULT '',
              text            TEXT NOT NULL,
              token_estimate  INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_symbol_path ON chunks(symbol_path);

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
              text, breadcrumb, symbol_path, language UNINDEXED, path UNINDEXED,
              content='', tokenize='unicode61'
            );

            CREATE TABLE IF NOT EXISTS symbols (
              id              INTEGER PRIMARY KEY,
              file_id         INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              name            TEXT NOT NULL,
              qualified_name  TEXT NOT NULL,
              kind            TEXT NOT NULL,
              line            INTEGER NOT NULL,
              col             INTEGER NOT NULL,
              signature       TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_symbols_qname ON symbols(qualified_name);
            CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);

            CREATE TABLE IF NOT EXISTS refs (
              id              INTEGER PRIMARY KEY,
              src_file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              src_line        INTEGER NOT NULL,
              ref_kind        TEXT NOT NULL,
              target_name     TEXT NOT NULL,
              target_file_id  INTEGER REFERENCES files(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_refs_target ON refs(target_name);
            CREATE INDEX IF NOT EXISTS idx_refs_src ON refs(src_file_id);
            """
        )

    # --- connection plumbing ---

    def raw_conn(self) -> sqlite3.Connection:
        """Return the underlying connection. Reserved for DenseBackend and
        graph expansion, which need ad-hoc SQL. Avoid in application code."""
        return self._conn

    @contextmanager
    def tx(self) -> Generator[sqlite3.Connection, None, None]:
        self._conn.execute("BEGIN")
        try:
            yield self._conn
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    @contextmanager
    def savepoint(self) -> Generator[sqlite3.Connection, None, None]:
        savepoint = f"sp_{time.time_ns()}"
        self._conn.execute(f"SAVEPOINT {savepoint}")
        try:
            yield self._conn
        except Exception:
            self._conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")

    def close(self) -> None:
        with suppress(sqlite3.ProgrammingError):
            self._conn.close()

    # --- write path: called by the indexer ---

    def upsert_file(
        self,
        *,
        path: str,
        language: str | None,
        size_bytes: int,
        mtime_ns: int,
        content_hash: str,
    ) -> int:
        now = int(time.time())
        row = self._conn.execute(
            "SELECT id, content_hash FROM files WHERE path = ?", (path,)
        ).fetchone()
        if row is None:
            cur = self._conn.execute(
                """
                INSERT INTO files(path, language, size_bytes, mtime_ns, content_hash, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (path, language, size_bytes, mtime_ns, content_hash, now),
            )
            rowid = cur.lastrowid
            if not rowid:  # SQLite rowids start at 1; 0/None is a failure
                raise RuntimeError(f"INSERT files returned no rowid for path={path!r}")
            return int(rowid)
        self._conn.execute(
            """
            UPDATE files SET language=?, size_bytes=?, mtime_ns=?, content_hash=?, indexed_at=?
            WHERE id=?
            """,
            (language, size_bytes, mtime_ns, content_hash, now, row["id"]),
        )
        return int(row["id"])

    def file_is_unchanged(self, path: str, content_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT content_hash FROM files WHERE path = ?", (path,)
        ).fetchone()
        return bool(row and row["content_hash"] == content_hash)

    def clear_file_derivatives(self, file_id: int) -> None:
        """Clear chunks/symbols/refs for a file before re-inserting."""
        # Clean up FTS entries first (chunks_fts rows keyed by chunk rowid).
        chunk_ids = [
            r["id"]
            for r in self._conn.execute(
                "SELECT id FROM chunks WHERE file_id = ?", (file_id,)
            ).fetchall()
        ]
        if chunk_ids:
            # Contentless FTS5 tables need explicit row-by-row deletes through
            # the shadow interface — `DELETE ... WHERE rowid IN (...)` is not
            # permitted. See https://www.sqlite.org/fts5.html#the_delete_command
            for cid in chunk_ids:
                self._conn.execute(
                    "INSERT INTO chunks_fts(chunks_fts, rowid, text, breadcrumb, symbol_path, language, path) "
                    "SELECT 'delete', c.id, c.text, c.breadcrumb, c.symbol_path, f.language, f.path "
                    "FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.id = ?",
                    (cid,),
                )
        self._conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
        self._conn.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))
        self._conn.execute("DELETE FROM refs WHERE src_file_id = ?", (file_id,))

    def insert_chunk(
        self,
        *,
        file_id: int,
        path: str,
        language: str | None,
        symbol_path: str,
        symbol_kind: str,
        start_line: int,
        end_line: int,
        byte_start: int,
        byte_end: int,
        breadcrumb: str,
        text: str,
        token_estimate: int,
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO chunks(file_id, symbol_path, symbol_kind, start_line, end_line,
                               byte_start, byte_end, breadcrumb, text, token_estimate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                symbol_path,
                symbol_kind,
                start_line,
                end_line,
                byte_start,
                byte_end,
                breadcrumb,
                text,
                token_estimate,
            ),
        )
        rowid = cur.lastrowid
        if not rowid:
            raise RuntimeError(f"INSERT chunks returned no rowid for file_id={file_id}")
        chunk_id = int(rowid)
        self._conn.execute(
            """
            INSERT INTO chunks_fts(rowid, text, breadcrumb, symbol_path, language, path)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chunk_id, text, breadcrumb, symbol_path, language or "", path),
        )
        return chunk_id

    def insert_symbol(
        self,
        *,
        file_id: int,
        name: str,
        qualified_name: str,
        kind: str,
        line: int,
        col: int,
        signature: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO symbols(file_id, name, qualified_name, kind, line, col, signature)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (file_id, name, qualified_name, kind, line, col, signature),
        )

    def insert_reference(
        self,
        *,
        src_file_id: int,
        src_line: int,
        ref_kind: str,
        target_name: str,
        target_file_id: int | None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO refs(src_file_id, src_line, ref_kind, target_name, target_file_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (src_file_id, src_line, ref_kind, target_name, target_file_id),
        )

    def delete_missing_files(self, present_paths: Iterable[str]) -> int:
        """Drop files no longer on disk. Return count removed."""
        rows = self._conn.execute("SELECT id, path FROM files").fetchall()
        present = set(present_paths)
        to_delete = [row["id"] for row in rows if row["path"] not in present]
        if to_delete:
            placeholders = ",".join("?" for _ in to_delete)
            chunk_ids = [
                r["id"]
                for r in self._conn.execute(
                    f"SELECT id FROM chunks WHERE file_id IN ({placeholders})", to_delete
                ).fetchall()
            ]
            if chunk_ids:
                for cid in chunk_ids:
                    self._conn.execute(
                        "INSERT INTO chunks_fts(chunks_fts, rowid, text, breadcrumb, symbol_path, language, path) "
                        "SELECT 'delete', c.id, c.text, c.breadcrumb, c.symbol_path, f.language, f.path "
                        "FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.id = ?",
                        (cid,),
                    )
            self._conn.execute(
                f"DELETE FROM files WHERE id IN ({placeholders})", to_delete
            )
        return len(to_delete)

    # --- read path ---

    def fts_search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        try:
            rows = self._conn.execute(
                """
                SELECT c.id AS chunk_id,
                       f.path AS path,
                       f.language AS language,
                       c.symbol_path, c.symbol_kind,
                       c.start_line, c.end_line, c.breadcrumb, c.text,
                       bm25(chunks_fts) AS score
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.rowid
                JOIN files  f ON f.id = c.file_id
                WHERE chunks_fts MATCH ?
                ORDER BY score ASC
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            lowered = str(exc).lower()
            kind = "query_invalid" if any(
                token in lowered for token in ("syntax", "malformed", "fts5", "match")
            ) else "index_unavailable"
            log.warning("fts_search_failed", query=query, kind=kind, error=str(exc))
            raise FTSQueryError(
                f"FTS search failed ({kind}): {exc}",
                kind=kind,
                query=query,
            ) from exc
        return [dict(row) for row in rows]

    def lookup_symbols(
        self,
        name: str,
        kind: str | None = None,
        limit: int = 20,
        *,
        match: str = "prefix",
    ) -> list[dict[str, Any]]:
        """Lookup symbols by name.

        `match` in {"exact", "prefix", "fuzzy"}. Default is "prefix" which
        uses the existing `idx_symbols_name` index efficiently. "fuzzy" falls
        back to `%name%` substring which is full-scan and should only be
        used on small indexes or when the caller explicitly asks for it.
        """
        params: list[Any] = []
        if match == "exact":
            sql = (
                "SELECT s.*, f.path AS path, f.language AS language "
                "FROM symbols s JOIN files f ON f.id = s.file_id "
                "WHERE (s.name = ? COLLATE NOCASE "
                "   OR s.qualified_name = ? COLLATE NOCASE)"
            )
            params.extend([name, name])
        elif match == "fuzzy":
            sql = (
                "SELECT s.*, f.path AS path, f.language AS language "
                "FROM symbols s JOIN files f ON f.id = s.file_id "
                "WHERE (s.name LIKE ? COLLATE NOCASE "
                "   OR s.qualified_name LIKE ? COLLATE NOCASE)"
            )
            params.extend([f"%{name}%", f"%{name}%"])
        else:  # prefix (default)
            sql = (
                "SELECT s.*, f.path AS path, f.language AS language "
                "FROM symbols s JOIN files f ON f.id = s.file_id "
                "WHERE (s.name LIKE ? COLLATE NOCASE "
                "   OR s.qualified_name LIKE ? COLLATE NOCASE)"
            )
            params.extend([f"{name}%", f"{name}%"])
        if kind:
            sql += " AND s.kind = ?"
            params.append(kind)
        sql += " ORDER BY length(s.name), s.name LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def find_references(
        self,
        target_name: str,
        limit: int = 50,
        *,
        match: str = "exact",
    ) -> list[dict[str, Any]]:
        """Find references to a symbol.

        `match` in {"exact", "prefix", "fuzzy"}. Default is "exact" because
        referencing a similarly-named but unrelated symbol gives the caller
        misleading provenance.
        """
        if match == "fuzzy":
            pattern = f"%{target_name}%"
            rows = self._conn.execute(
                """
                SELECT r.ref_kind, r.target_name, r.src_line,
                       f.path AS src_path, f.language AS language
                FROM refs r JOIN files f ON f.id = r.src_file_id
                WHERE r.target_name LIKE ?
                ORDER BY f.path, r.src_line LIMIT ?
                """,
                (pattern, limit),
            ).fetchall()
        elif match == "prefix":
            rows = self._conn.execute(
                """
                SELECT r.ref_kind, r.target_name, r.src_line,
                       f.path AS src_path, f.language AS language
                FROM refs r JOIN files f ON f.id = r.src_file_id
                WHERE r.target_name LIKE ?
                ORDER BY f.path, r.src_line LIMIT ?
                """,
                (f"{target_name}%", limit),
            ).fetchall()
        else:  # exact
            rows = self._conn.execute(
                """
                SELECT r.ref_kind, r.target_name, r.src_line,
                       f.path AS src_path, f.language AS language
                FROM refs r JOIN files f ON f.id = r.src_file_id
                WHERE r.target_name = ?
                ORDER BY f.path, r.src_line LIMIT ?
                """,
                (target_name, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_file_row(self, path: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM files WHERE path = ?", (path,)
        ).fetchone()
        return dict(row) if row else None

    def get_chunk(self, chunk_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT c.*, f.path AS path, f.language AS language
            FROM chunks c JOIN files f ON f.id = c.file_id
            WHERE c.id = ?
            """,
            (chunk_id,),
        ).fetchone()
        return dict(row) if row else None

    def file_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()
        return int(row["n"])

    def chunk_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()
        return int(row["n"])

    def symbol_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM symbols").fetchone()
        return int(row["n"])

    def last_indexed_at(self) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(indexed_at) AS t FROM files"
        ).fetchone()
        return int(row["t"]) if row and row["t"] is not None else None

    def languages_indexed(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT language, COUNT(*) AS n FROM files GROUP BY language"
        ).fetchall()
        return {row["language"] or "unknown": int(row["n"]) for row in rows}

    def recent_files(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT path, language, size_bytes, indexed_at "
            "FROM files ORDER BY indexed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
