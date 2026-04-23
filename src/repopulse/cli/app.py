"""Typer-based CLI: `repopulse ...`."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import typer

from repopulse import __version__
from repopulse.cli.format import render_search, render_trace, resolve_format
from repopulse.config import load_settings
from repopulse.logging import configure as configure_logging
from repopulse.paths import ensure_state_dir, find_repo_root, index_db_path, state_dir

app = typer.Typer(
    name="repopulse",
    help="Local-first code index with a visible retrieval trace.",
    add_completion=False,
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"repopulse {__version__}")
        raise typer.Exit()


def _resolve_repo_arg(repo: Path) -> Path:
    candidate = repo.resolve()
    if candidate == Path.cwd().resolve():
        return find_repo_root(candidate).resolve()
    return candidate


@app.callback()
def main_callback(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """RepoPulse — local code index for coding agents."""
    _ = version  # handled by callback.


@app.command()
def init(
    repo: Path = typer.Option(Path.cwd(), "--repo", "-r", help="Repo root."),
) -> None:
    """Create `.repopulse/` and a stub config."""
    root = _resolve_repo_arg(repo)
    ensure_state_dir(root)
    typer.echo(f"Initialized RepoPulse state at {state_dir(root)}")


@app.command("index")
def index_cmd(
    repo: Path = typer.Option(Path.cwd(), "--repo", "-r", help="Repo root."),
    force: bool = typer.Option(False, "--force", help="Re-index every file."),
    embeddings: bool = typer.Option(
        False,
        "--embeddings/--no-embeddings",
        help="Enable local fastembed embeddings (requires extras).",
    ),
) -> None:
    """Index the repo into `.repopulse/index.db`."""
    configure_logging(mode="cli")
    from repopulse.indexer.run import index_repo

    root = _resolve_repo_arg(repo)
    settings = load_settings(root)
    if embeddings:
        settings.embeddings.enabled = True
    stats = index_repo(root, force=force, settings=settings)
    typer.echo(
        f"Indexed {stats.files_indexed} files "
        f"({stats.files_unchanged} unchanged, {stats.files_removed} removed) "
        f"-> {stats.chunks_written} chunks, {stats.symbols_written} symbols, "
        f"{stats.references_written} refs in {stats.duration_ms}ms"
    )
    if stats.errors:
        typer.echo(f"{len(stats.errors)} errors (first shown):", err=True)
        for err in stats.errors[:5]:
            typer.echo(f"  {err}", err=True)


@app.command()
def search(
    query: str = typer.Argument(..., help="Query text."),
    repo: Path = typer.Option(Path.cwd(), "--repo", "-r", help="Repo root."),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=50),
    mode: str = typer.Option(
        "hybrid",
        "--mode",
        help="hybrid | fts | dense",
        case_sensitive=False,
    ),
    fmt: str = typer.Option(
        "auto",
        "--format",
        "-f",
        help="auto | rich | plain | json",
        case_sensitive=False,
    ),
) -> None:
    """Hybrid search across the indexed repo."""
    configure_logging(mode="cli")
    from repopulse.retrieval.dense import DenseBackend
    from repopulse.retrieval.hybrid import search as run_search
    from repopulse.retrieval.store import Store

    root = _resolve_repo_arg(repo)
    settings = load_settings(root)
    db = index_db_path(root)
    if not db.exists():
        typer.echo(
            "No index found. Run `repopulse index` first.", err=True
        )
        raise typer.Exit(code=2)
    store = Store(db)
    dense = None
    if settings.embeddings.enabled:
        backend = DenseBackend(
            conn=store.raw_conn(),
            model_name=settings.embeddings.model_name,
        )
        dense = backend if backend.available else None
    try:
        response = run_search(
            store,
            query,
            limit=limit,
            mode=mode.lower(),
            settings=settings.retrieval,
            dense_backend=dense,
        )
    finally:
        store.close()

    opts = resolve_format(fmt)
    if opts.output_format == "json":
        payload = {
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
                    "components": r.components,
                }
                for r in response.results
            ],
            "candidates_considered": response.candidates_considered,
            "duration_ms": response.duration_ms,
            "sufficiency": response.sufficiency,
            "modes_used": response.modes_used,
        }
        typer.echo(json.dumps(payload, indent=2))
        return
    render_search(response, opts)


@app.command()
def trace(
    trace_id: str = typer.Argument(..., help="Trace id like 'rp_7fa3'."),
    repo: Path = typer.Option(Path.cwd(), "--repo", "-r", help="Repo root."),
    fmt: str = typer.Option("auto", "--format", "-f"),
) -> None:
    """Replay a prior retrieval trace."""
    configure_logging(mode="cli")
    from repopulse.retrieval.store import Store

    root = _resolve_repo_arg(repo)
    store = Store(index_db_path(root))
    try:
        record = store.traces.get_trace(trace_id)
    finally:
        store.close()
    if record is None:
        typer.echo(f"No trace with id {trace_id!r}", err=True)
        raise typer.Exit(code=1)
    opts = resolve_format(fmt)
    if opts.output_format == "json":
        typer.echo(json.dumps(record, indent=2, default=str))
        return
    render_trace(record, opts)


@app.command()
def bad(
    trace_id: str = typer.Argument(..., help="Trace id to flag as bad."),
    reason: str | None = typer.Option(None, "--reason", help="Short note for why."),
    repo: Path = typer.Option(Path.cwd(), "--repo", "-r", help="Repo root."),
) -> None:
    """Flag a trace as having led to a bad agent action.

    The trace is preserved; the label seeds future retrieval tuning.
    This is your corrective channel into the system.
    """
    configure_logging(mode="cli")
    from repopulse.retrieval.store import Store

    root = _resolve_repo_arg(repo)
    store = Store(index_db_path(root))
    try:
        ok = store.traces.mark_trace_bad(trace_id, reason)
    finally:
        store.close()
    if not ok:
        typer.echo(f"No trace with id {trace_id!r}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Flagged {trace_id} as bad. Review with: repopulse bad-list")


@app.command("bad-list")
def bad_list(
    repo: Path = typer.Option(Path.cwd(), "--repo", "-r", help="Repo root."),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=500),
) -> None:
    """List traces flagged as bad (most recent first)."""
    configure_logging(mode="cli")
    from repopulse.retrieval.store import Store

    root = _resolve_repo_arg(repo)
    store = Store(index_db_path(root))
    try:
        rows = store.traces.list_bad_traces(limit=limit)
    finally:
        store.close()
    if not rows:
        typer.echo("No bad traces flagged yet.")
        return
    typer.echo(json.dumps(rows, indent=2, default=str))


@app.command()
def doctor(
    repo: Path = typer.Option(Path.cwd(), "--repo", "-r", help="Repo root."),
) -> None:
    """Print a summary of index health."""
    configure_logging(mode="cli")
    from repopulse.retrieval.store import Store

    root = _resolve_repo_arg(repo)
    db = index_db_path(root)
    if not db.exists():
        typer.echo("No index. Run `repopulse index` first.", err=True)
        raise typer.Exit(code=2)
    store = Store(db)
    try:
        info = {
            "repo_root": str(root),
            "db_path": str(db),
            "files": store.file_count(),
            "chunks": store.chunk_count(),
            "symbols": store.symbol_count(),
            "languages": store.languages_indexed(),
            "last_indexed_at": store.last_indexed_at(),
        }
    finally:
        store.close()
    typer.echo(json.dumps(info, indent=2))


@app.command()
def serve(
    repo: Path = typer.Option(Path.cwd(), "--repo", "-r", help="Repo root to serve."),
) -> None:
    """Run the MCP stdio server."""
    configure_logging(mode="mcp")
    from repopulse.mcp_server.server import run_stdio

    root = _resolve_repo_arg(repo)
    run_stdio(repo_root=root)


@app.command()
def install(
    repo: Path = typer.Option(Path.cwd(), "--repo", "-r", help="Repo root."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Install without prompts."),
) -> None:
    """Detect coding-agent configs and register RepoPulse as an MCP server."""
    configure_logging(mode="cli")
    from repopulse.cli import install as installer

    root = _resolve_repo_arg(repo)
    targets = installer.candidate_targets()
    typer.echo(f"Detected candidate agent configs for {root}:")
    chosen: list[installer.AgentTarget] = []
    for target in targets:
        exists = target.path.exists()
        status = "exists" if exists else "would create"
        if yes:
            typer.echo(f"  [install] {target.name:<22} {target.path}  ({status})")
            chosen.append(target)
            continue
        prompt = f"  Install into {target.name} ({target.path}) [{status}]? [y/N] "
        answer = typer.prompt(prompt, default="n", show_default=False)
        if answer.strip().lower() in {"y", "yes"}:
            chosen.append(target)
    if not chosen:
        typer.echo("Nothing installed.")
        return
    failures = 0
    for target in chosen:
        try:
            installer.install_target(target, root)
        except (OSError, ValueError) as exc:
            failures += 1
            typer.echo(f"  [error] {target.name}: {exc}", err=True)
            continue
        typer.echo(f"  [ok] {target.name}: {target.path}")
    if failures:
        raise typer.Exit(code=1)


def _enable_utf8_stdout() -> None:
    # Windows terminals historically default to cp1252 which breaks any
    # non-ASCII output (typer.echo, rich panels with '·' or em-dash, etc.).
    # Python 3.7+ added `reconfigure`; try it best-effort.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(Exception):
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _enable_utf8_stdout()
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
