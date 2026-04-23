"""Index a repository: walker -> chunker -> symbols -> refs -> store."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from repopulse.config import IndexerSettings, Settings
from repopulse.indexer.chunker import ChunkOptions, chunk_source
from repopulse.indexer.hasher import content_hash
from repopulse.indexer.symbols import extract_references, extract_symbols
from repopulse.indexer.walker import walk
from repopulse.languages import parse
from repopulse.logging import get_logger
from repopulse.paths import ensure_state_dir, index_db_path
from repopulse.retrieval.dense import DenseBackend
from repopulse.retrieval.store import Store

log = get_logger("indexer")
_INDEX_WRITE_BATCH_SIZE = 200


@dataclass
class IndexStats:
    files_seen: int = 0
    files_indexed: int = 0
    files_unchanged: int = 0
    files_removed: int = 0
    chunks_written: int = 0
    symbols_written: int = 0
    references_written: int = 0
    duration_ms: int = 0
    errors: list[str] = field(default_factory=list)
    embeddings_written: int = 0


def index_repo(
    repo_root: Path,
    *,
    force: bool = False,
    settings: Settings | None = None,
) -> IndexStats:
    """Run a full (or incremental if not `force`) index over `repo_root`.

    Incremental: a file is re-indexed only when its content hash differs from
    the stored hash. Files removed from disk are purged from the DB.
    """
    repo_root = repo_root.resolve()
    settings = settings or Settings()
    ensure_state_dir(repo_root)

    store = Store(index_db_path(repo_root))
    dense = _maybe_dense_backend(store, settings)

    stats = IndexStats()
    t0 = time.perf_counter()
    seen_paths: list[str] = []
    pending: list[_PendingFile] = []
    chunk_opts = ChunkOptions(
        max_bytes=settings.indexer.max_chunk_bytes,
        min_bytes=settings.indexer.min_chunk_bytes,
    )

    try:
        for walked in walk(repo_root, settings.indexer, errors=stats.errors):
            stats.files_seen += 1
            seen_paths.append(walked.relative_posix)
            try:
                raw = walked.path.read_bytes()
            except OSError as exc:
                stats.errors.append(f"{walked.relative_posix}: read failed: {exc}")
                continue
            digest = content_hash(raw)
            if not force and store.file_is_unchanged(walked.relative_posix, digest):
                stats.files_unchanged += 1
                continue
            pending.append(
                _PendingFile(
                    walked_path=walked.relative_posix,
                    language=walked.language,
                    size_bytes=walked.size_bytes,
                    mtime_ns=walked.mtime_ns,
                    content=raw,
                    digest=digest,
                )
            )
            if len(pending) >= _INDEX_WRITE_BATCH_SIZE:
                written = _flush_batch(store, dense, pending, chunk_opts, stats.errors)
                stats.files_indexed += written.files
                stats.chunks_written += written.chunks
                stats.symbols_written += written.symbols
                stats.references_written += written.references
                stats.embeddings_written += written.embeddings
                pending.clear()
        if pending:
            written = _flush_batch(store, dense, pending, chunk_opts, stats.errors)
            stats.files_indexed += written.files
            stats.chunks_written += written.chunks
            stats.symbols_written += written.symbols
            stats.references_written += written.references
            stats.embeddings_written += written.embeddings
        with store.tx():
            stats.files_removed = store.delete_missing_files(seen_paths)
    finally:
        store.close()

    stats.duration_ms = int((time.perf_counter() - t0) * 1000)
    log.info(
        "index_complete",
        files_seen=stats.files_seen,
        files_indexed=stats.files_indexed,
        files_unchanged=stats.files_unchanged,
        files_removed=stats.files_removed,
        chunks=stats.chunks_written,
        symbols=stats.symbols_written,
        references=stats.references_written,
        embeddings=stats.embeddings_written,
        duration_ms=stats.duration_ms,
    )
    return stats


@dataclass
class _FileWriteStats:
    files: int = 0
    chunks: int = 0
    symbols: int = 0
    references: int = 0
    embeddings: int = 0


@dataclass
class _PendingFile:
    walked_path: str
    language: str | None
    size_bytes: int
    mtime_ns: int
    content: bytes
    digest: str


def _flush_batch(
    store: Store,
    dense: DenseBackend | None,
    pending: list[_PendingFile],
    chunk_opts: ChunkOptions,
    errors: list[str],
) -> _FileWriteStats:
    batch = _FileWriteStats()
    with store.tx():
        for entry in pending:
            try:
                written = _index_file(
                    store=store,
                    dense=dense,
                    walked_path=entry.walked_path,
                    language=entry.language,
                    size_bytes=entry.size_bytes,
                    mtime_ns=entry.mtime_ns,
                    content=entry.content,
                    digest=entry.digest,
                    chunk_opts=chunk_opts,
                )
            except Exception as exc:
                errors.append(f"{entry.walked_path}: index failed: {exc}")
                continue
            batch.files += 1
            batch.chunks += written.chunks
            batch.symbols += written.symbols
            batch.references += written.references
            batch.embeddings += written.embeddings
    return batch


def _index_file(
    *,
    store: Store,
    dense: DenseBackend | None,
    walked_path: str,
    language: str | None,
    size_bytes: int,
    mtime_ns: int,
    content: bytes,
    digest: str,
    chunk_opts: ChunkOptions,
) -> _FileWriteStats:
    stats = _FileWriteStats()
    with store.savepoint():
        tree = parse(content, language) if language else None
        file_id = store.upsert_file(
            path=walked_path,
            language=language,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            content_hash=digest,
        )
        # Delete dense vectors for the old chunks before clearing the chunks
        # table, otherwise stale chunk ids accumulate in the embedding cache.
        if dense and dense.available:
            old_chunk_ids = [
                row_id
                for (row_id,) in store.raw_conn().execute(
                    "SELECT id FROM chunks WHERE file_id = ?", (file_id,)
                ).fetchall()
            ]
            if old_chunk_ids:
                dense.delete(old_chunk_ids)
        store.clear_file_derivatives(file_id)
        for chunk in chunk_source(content, language, chunk_opts, tree=tree):
            store.insert_chunk(
                file_id=file_id,
                path=walked_path,
                language=language,
                symbol_path=chunk.symbol_path,
                symbol_kind=chunk.symbol_kind,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                byte_start=chunk.byte_start,
                byte_end=chunk.byte_end,
                breadcrumb=chunk.breadcrumb,
                text=chunk.text,
                token_estimate=_rough_token_estimate(chunk.text),
            )
            stats.chunks += 1
        for symbol in extract_symbols(content, language, tree=tree):
            store.insert_symbol(
                file_id=file_id,
                name=symbol.name,
                qualified_name=symbol.qualified_name,
                kind=symbol.kind,
                line=symbol.line,
                col=symbol.col,
                signature=symbol.signature,
            )
            stats.symbols += 1
        for ref in extract_references(content, language, tree=tree):
            store.insert_reference(
                src_file_id=file_id,
                src_line=ref.src_line,
                ref_kind=ref.ref_kind,
                target_name=ref.target_name,
                target_file_id=None,
            )
            stats.references += 1

    return stats


def _rough_token_estimate(text: str) -> int:
    # ~4 chars/token is a reasonable default for English + code.
    return max(1, len(text) // 4)


def _maybe_dense_backend(store: Store, settings: Settings) -> DenseBackend | None:
    if not settings.embeddings.enabled:
        return None
    try:
        backend = DenseBackend(
            conn=store.raw_conn(),
            model_name=settings.embeddings.model_name,
            batch_size=settings.embeddings.batch_size,
        )
    except Exception as exc:  # pragma: no cover
        log.warning("dense_backend_init_failed", error=str(exc))
        return None
    if not backend.available:
        return None
    return backend


def reset_index(repo_root: Path) -> None:
    """Delete the local index. Useful when schema changes or data is corrupt."""
    db_path = index_db_path(repo_root)
    for suffix in ("", "-wal", "-shm"):
        candidate = db_path.with_name(db_path.name + suffix)
        if candidate.exists():
            candidate.unlink()


_SettingsForIndexer = IndexerSettings  # re-export for convenience.
