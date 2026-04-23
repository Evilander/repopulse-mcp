"""FastMCP server exposing RepoPulse tools over stdio.

Uses the FastMCP helper that ships with the official `mcp` SDK (lives at
`mcp.server.fastmcp` — the same package name the MCP python-sdk README uses).
"""

from __future__ import annotations

import atexit
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from mcp.types import ToolAnnotations

from repopulse.config import Settings, load_settings
from repopulse.content_safety import detect_sensitive_content
from repopulse.logging import configure as configure_logging
from repopulse.logging import get_logger
from repopulse.paths import find_repo_root, index_db_path
from repopulse.retrieval.dense import DenseBackend
from repopulse.retrieval.hybrid import search
from repopulse.retrieval.store import Store
from repopulse.trace.sufficiency import confidence_label

log = get_logger("mcp")


@dataclass
class _ServerRuntime:
    repo_root: Path
    store: Store
    dense: DenseBackend | None
    settings: Settings

    def close(self) -> None:
        self.store.close()


def _open_store_and_backend(repo_root: Path) -> _ServerRuntime:
    settings = load_settings(repo_root)
    store = Store(index_db_path(repo_root))
    dense: DenseBackend | None = None
    if settings.embeddings.enabled:
        try:
            dense_backend = DenseBackend(
                conn=store.raw_conn(),
                model_name=settings.embeddings.model_name,
                batch_size=settings.embeddings.batch_size,
            )
            dense = dense_backend if dense_backend.available else None
        except Exception as exc:
            log.warning("dense_init_failed", error=str(exc))
    return _ServerRuntime(repo_root=repo_root, store=store, dense=dense, settings=settings)


def _annotations(
    *,
    read_only: bool,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool = False,
) -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


def _resolve_safe_path(repo_root: Path, user_path: str) -> Path:
    """Resolve `user_path` inside `repo_root`; reject traversal outside."""
    raw_path = PureWindowsPath(user_path)
    if Path(user_path).is_absolute() or raw_path.is_absolute() or raw_path.drive:
        raise ValueError(f"Absolute paths are not allowed: {user_path!r}")
    candidate = (repo_root / user_path).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ValueError(f"Path {user_path!r} resolves outside the indexed repo") from exc
    return candidate


def build_server(repo_root: Path | None = None):  # type: ignore[no-untyped-def]
    """Construct the FastMCP server instance for this repo."""
    from mcp.server.fastmcp import FastMCP

    configure_logging(mode="mcp")
    root = (repo_root or find_repo_root()).resolve()
    log.info("mcp_server_starting", repo_root=str(root))
    runtime = _open_store_and_backend(root)
    atexit.register(runtime.close)
    server = FastMCP(
        "repopulse",
        instructions=(
            "Local-first code index with a visible retrieval trace. "
            "Every search returns a trace_id you can replay with "
            "get_context_trace or explain_last_result to see what was "
            "considered and why. Call index_repo once, then use search_code "
            "and find_symbol. read_file returns a line range of any indexed file."
        ),
    )

    @server.tool(
        name="index_repo",
        description=(
            "Index or re-index the current repository. Run once per repo, "
            "or again after a large refactor. Cheap if nothing changed "
            "(content-hash based incremental)."
        ),
        annotations=_annotations(read_only=False, idempotent=True),
    )
    def index_repo_tool(force: bool = False) -> dict[str, Any]:
        from repopulse.indexer.run import index_repo as run_index

        stats = run_index(root, force=force, settings=runtime.settings)
        return {
            "repo_root": str(root),
            "files_seen": stats.files_seen,
            "files_indexed": stats.files_indexed,
            "files_unchanged": stats.files_unchanged,
            "files_removed": stats.files_removed,
            "chunks": stats.chunks_written,
            "symbols": stats.symbols_written,
            "references": stats.references_written,
            "embeddings": stats.embeddings_written,
            "duration_ms": stats.duration_ms,
            "errors": stats.errors[:10],
        }

    @server.tool(
        description=(
            "Hybrid search across the repo. Returns ranked chunks with file "
            "path, line range, symbol path, text, and a score. Every call "
            "records a trace_id you can inspect with get_context_trace."
        ),
        annotations=_annotations(read_only=True),
    )
    def search_code(
        query: str,
        limit: int = 10,
        mode: str = "hybrid",
    ) -> dict[str, Any]:
        response = search(
            runtime.store,
            query,
            limit=max(1, min(limit, 50)),
            mode=mode if mode in {"hybrid", "fts", "dense"} else "hybrid",
            settings=runtime.settings.retrieval,
            dense_backend=runtime.dense,
        )
        return {
            "trace_id": response.trace_id,
            "query": response.query,
            "results": [
                {
                    "path": r.path,
                    "language": r.language,
                    "symbol_path": r.symbol_path,
                    "symbol_kind": r.symbol_kind,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                    "score": r.score,
                    "breadcrumb": r.breadcrumb,
                    "text": r.text,
                    "score_components": r.components,
                }
                for r in response.results
            ],
            "candidates_considered": response.candidates_considered,
            "duration_ms": response.duration_ms,
            "sufficiency": {
                "score": round(response.sufficiency, 3),
                "label": confidence_label(response.sufficiency),
            },
            "modes_used": response.modes_used,
            "replay_hint": f"get_context_trace(trace_id='{response.trace_id}')",
        }

    @server.tool(
        description=(
            "Look up symbols by name (supports partial match). Use this for "
            "precise navigation when you know the function/class name; use "
            "search_code for concept queries."
        ),
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def find_symbol(
        name: str,
        kind: str | None = None,
        limit: int = 20,
        match: str = "prefix",
    ) -> dict[str, Any]:
        from repopulse.trace.writer import SimpleTrace, timer, write

        _, elapsed = timer()
        rows = runtime.store.lookup_symbols(
            name,
            kind=kind,
            limit=max(1, min(limit, 200)),
            match=match if match in {"exact", "prefix", "fuzzy"} else "prefix",
        )
        matches = [
            {
                "name": row["name"],
                "qualified_name": row["qualified_name"],
                "kind": row["kind"],
                "path": row["path"],
                "language": row["language"],
                "line": row["line"],
                "col": row["col"],
                "signature": row["signature"],
            }
            for row in rows
        ]
        trace_id = write(
            runtime.store,
            SimpleTrace(
                tool_name="find_symbol",
                query=name,
                params={"kind": kind, "limit": limit, "match": match},
                items=[{**m, "score": 1.0 - (i / max(len(matches), 1))} for i, m in enumerate(matches)],
                sufficiency=1.0 if matches else 0.0,
                notes={"duration_ms": elapsed()},
            ),
        )
        return {
            "query": name,
            "kind": kind,
            "match": match,
            "trace_id": trace_id,
            "matches": matches,
        }

    @server.tool(
        description=(
            "Find places that reference a given symbol name (imports + calls)."
        ),
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def find_references(
        name: str, limit: int = 50, match: str = "exact"
    ) -> dict[str, Any]:
        from repopulse.trace.writer import SimpleTrace, timer, write

        _, elapsed = timer()
        rows = runtime.store.find_references(
            name,
            limit=max(1, min(limit, 500)),
            match=match if match in {"exact", "prefix", "fuzzy"} else "exact",
        )
        refs = [
            {
                "ref_kind": row["ref_kind"],
                "target_name": row["target_name"],
                "path": row["src_path"],
                "line": row["src_line"],
                "language": row["language"],
            }
            for row in rows
        ]
        trace_id = write(
            runtime.store,
            SimpleTrace(
                tool_name="find_references",
                query=name,
                params={"limit": limit, "match": match},
                items=[{**r, "score": 1.0} for r in refs],
                sufficiency=1.0 if refs else 0.0,
                notes={"duration_ms": elapsed()},
            ),
        )
        return {
            "target": name,
            "match": match,
            "trace_id": trace_id,
            "references": refs,
        }

    @server.tool(
        description=(
            "Read a range of lines from an indexed file. Paths are relative "
            "to the indexed repo root. Start/end are 1-indexed and inclusive."
        ),
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def read_file(
        path: str, start_line: int = 1, end_line: int | None = None
    ) -> dict[str, Any]:
        from repopulse.trace.writer import SimpleTrace, timer, write

        abs_path = _resolve_safe_path(root, path)
        if not abs_path.exists() or not abs_path.is_file():
            raise FileNotFoundError(f"No file at {path!r}")
        MAX_READ_BYTES = 10 * 1024 * 1024
        try:
            size = abs_path.stat().st_size
        except OSError as exc:
            raise OSError(f"Cannot stat {path!r}: {exc}") from exc
        if size > MAX_READ_BYTES:
            raise ValueError(
                f"File {path!r} is {size} bytes, over the {MAX_READ_BYTES} byte read cap"
            )
        _, elapsed = timer()
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise OSError(f"Cannot read {path!r}: {exc}") from exc
        if reason := detect_sensitive_content(content):
            raise PermissionError(
                f"File {path!r} looks like sensitive content ({reason}) and will not be served"
            )
        lines = content.splitlines()
        total = len(lines)
        start = max(1, start_line)
        end = min(total, end_line) if end_line else total
        if start > end:
            start, end = end, start
        selected = lines[start - 1 : end]

        trace_id = write(
            runtime.store,
            SimpleTrace(
                tool_name="read_file",
                query=path,
                params={"start_line": start, "end_line": end},
                items=[{"path": path, "start_line": start, "end_line": end, "score": 1.0}],
                sufficiency=1.0,
                notes={"duration_ms": elapsed(), "total_lines": total, "bytes_read": size},
            ),
        )

        return {
            "path": path,
            "start_line": start,
            "end_line": end,
            "total_lines": total,
            "trace_id": trace_id,
            "content": "\n".join(selected),
        }

    @server.tool(
        description=(
            "Replay a prior retrieval call: shows every chunk that was "
            "considered (not just returned) with per-source ranks and scores."
        ),
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def get_context_trace(trace_id: str) -> dict[str, Any]:
        record = runtime.store.traces.get_trace(trace_id)
        if record is None:
            raise ValueError(f"No trace with id {trace_id!r}")
        return record

    @server.tool(
        description=(
            "Shortcut: return the most recent retrieval trace for this repo. "
            "Useful right after a search_code call when the caller didn't "
            "track the trace_id themselves."
        ),
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def explain_last_result() -> dict[str, Any]:
        tid = runtime.store.traces.latest_trace_id()
        if tid is None:
            return {"trace": None, "items": [], "note": "no traces yet"}
        record = runtime.store.traces.get_trace(tid)
        return record or {"trace": None, "items": []}

    @server.tool(
        description=(
            "Given a query and the last trace, surface likely gaps: similar "
            "symbols you didn't return, low-score signals, and language "
            "coverage warnings."
        ),
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def diagnose_missing_context(
        query: str, trace_id: str | None = None
    ) -> dict[str, Any]:
        tid = trace_id or runtime.store.traces.latest_trace_id()
        record = runtime.store.traces.get_trace(tid) if tid else None
        hints: list[str] = []
        if record is None:
            hints.append("No prior trace - run search_code first to ground this.")
        else:
            score = float(record["trace"].get("sufficiency_score") or 0)
            if score < 0.35:
                hints.append(
                    f"Sufficiency score {score:.2f} is low - retrieval likely missed context."
                )
            elif score < 0.6:
                hints.append(
                    f"Sufficiency score {score:.2f} is uncertain - consider widening `limit` or rephrasing."
                )
            if record["trace"]["result_count"] == 0:
                hints.append("Zero results returned. Check indexing status and file extensions.")
        from repopulse.retrieval.fts import tokenize_query

        suggestions: list[dict[str, Any]] = []
        for tok in tokenize_query(query):
            matches = runtime.store.lookup_symbols(tok, limit=5)
            if matches:
                suggestions.append(
                    {
                        "token": tok,
                        "candidates": [
                            {
                                "name": m["name"],
                                "kind": m["kind"],
                                "path": m["path"],
                                "line": m["line"],
                            }
                            for m in matches
                        ],
                    }
                )
        if not suggestions and (record is None or record["trace"]["result_count"] == 0):
            hints.append(
                "None of the query tokens match any indexed symbol - either the repo isn't indexed yet, or the identifiers are from a different project."
            )
        return {
            "query": query,
            "hints": hints,
            "candidate_symbols": suggestions,
        }

    @server.tool(
        description=(
            "Summarize the health of the local index: file/chunk/symbol "
            "counts, language coverage, staleness, and recent trace stats."
        ),
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def get_index_health() -> dict[str, Any]:
        file_count = runtime.store.file_count()
        chunk_count = runtime.store.chunk_count()
        symbol_count = runtime.store.symbol_count()
        languages = runtime.store.languages_indexed()
        last = runtime.store.last_indexed_at()
        traces = runtime.store.traces.list_traces(limit=5)
        age_seconds = None if last is None else int(time.time() - last)
        return {
            "repo_root": str(root),
            "files": file_count,
            "chunks": chunk_count,
            "symbols": symbol_count,
            "languages": languages,
            "last_indexed_at": last,
            "index_age_seconds": age_seconds,
            "recent_traces": traces,
            "healthy": file_count > 0 and chunk_count > 0,
        }

    @server.tool(
        description=(
            "Mark a trace as having led to a bad agent action (wrong file, "
            "missing context). The trace is preserved and flagged; future "
            "work will use these labels to tune retrieval. This is the "
            "user's only corrective channel into the system."
        ),
        annotations=_annotations(read_only=False, idempotent=True),
    )
    def mark_bad_trace(trace_id: str, reason: str | None = None) -> dict[str, Any]:
        safe_reason = reason if reason is None or len(reason) <= 1024 else reason[:1021] + "..."
        ok = runtime.store.traces.mark_trace_bad(trace_id, safe_reason)
        return {"trace_id": trace_id, "updated": ok, "reason": safe_reason}

    @server.tool(
        description="List recent traces the user marked as bad.",
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def list_bad_traces(limit: int = 20) -> dict[str, Any]:
        rows = runtime.store.traces.list_bad_traces(limit=max(1, min(limit, 500)))
        return {"bad_traces": rows, "count": len(rows)}

    @server.tool(
        description="Briefly list indexed files (relative paths), newest first.",
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def list_indexed_files(limit: int = 50) -> dict[str, Any]:
        rows = runtime.store.recent_files(limit=max(1, min(limit, 500)))
        return {
            "files": rows,
            "shown": len(rows),
        }

    return server


def run_stdio(repo_root: Path | None = None) -> None:
    server = build_server(repo_root=repo_root)
    server.run(transport="stdio")
