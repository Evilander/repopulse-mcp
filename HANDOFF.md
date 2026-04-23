# RepoPulse MCP Handoff

## What This Repo Is

RepoPulse is a Python MCP server and CLI for local code retrieval. It indexes a repository into SQLite, exposes retrieval tools over stdio MCP, and persists a trace for every context-returning tool so retrieval can be debugged after the fact.

## Current Release State

- Local indexing, CLI flow, and MCP contract tests pass.
- Config file support now exists at `.repopulse/config.toml`.
- Install paths are fail-closed on invalid JSON or TOML instead of overwriting user configs.
- The MCP server now reuses one SQLite connection and one dense backend per process instead of reopening them on every tool call.
- Secret-looking files are skipped from indexing, and `read_file` refuses to serve secret-looking content.

## Core Paths

- `src/repopulse/cli/`
  CLI entrypoints and MCP client install helpers.
- `src/repopulse/indexer/`
  Walker, chunker, symbols, and repository indexing pipeline.
- `src/repopulse/retrieval/`
  SQLite store, FTS, dense backend, graph expansion, and hybrid search pipeline.
- `src/repopulse/mcp_server/server.py`
  FastMCP tool registration and stdio runtime.
- `src/repopulse/trace/`
  Trace ids, sufficiency heuristics, and trace persistence.
- `tests/`
  Contract, story, regression, and safety coverage.

## Working Validation Commands

```bash
python -m pytest -q
python -m ruff check src tests
python -m mypy src
```

## Remaining Follow-Up Work

- Add async MCP handlers or background indexing so a large first index does not block stdio for long stretches.
- Consider simplifying `retrieval/hybrid.py` if the stage abstraction is not going to support a second pipeline soon.
- Consider a stronger secret detector if false negatives matter more than conservative reads.
- Add a release workflow if PyPI publication becomes a real goal.
