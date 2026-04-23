# RepoPulse MCP handoff

For an agent picking this repo up cold.

## Snapshot

- Repo type: Python CLI + stdio MCP server
- Main claim: local code retrieval with replayable traces
- Validation at handoff: `pytest -q` => 74 passed, 1 skipped; `ruff check src tests` => passed; `mypy src` => passed
- Branch/workflow state: CI now runs on `master` and `main`

## What Changed In The Release-Fix Pass

- Fail-closed install logic for JSON and Codex TOML configs
- Config file support at `.repopulse/config.toml`
- Cached MCP store and dense backend per server process
- Dense backend logs exact failure points instead of silently disappearing
- Ruby `require` handling separated from Ruby call refs
- Byte-accurate fallback chunk offsets on non-UTF-8 files
- Batched index writes with parse-once per file
- Explicit FTS failure surface instead of silent `[]`
- Walker error reporting and secret-content skipping
- Trace query and feedback caps plus rolling retention
- `read_file` absolute-path rejection and secret-content blocking

## File Map

- `src/repopulse/cli/app.py`
  CLI commands.
- `src/repopulse/cli/install.py`
  MCP client install helpers.
- `src/repopulse/indexer/run.py`
  Repo indexing pipeline.
- `src/repopulse/retrieval/hybrid.py`
  Retrieval pipeline and trace persistence.
- `src/repopulse/mcp_server/server.py`
  FastMCP tool registration and runtime.
- `src/repopulse/trace/store.py`
  Trace persistence, clipping, and retention.

## Current MCP Tool Surface

- `index_repo`
- `search_code`
- `find_symbol`
- `find_references`
- `read_file`
- `get_context_trace`
- `explain_last_result`
- `diagnose_missing_context`
- `get_index_health`
- `mark_bad_trace`
- `list_bad_traces`
- `list_indexed_files`

## Next Reasonable Tasks

- Add async or background indexing for very large repos.
- Decide whether the retrieval stage abstraction stays or gets flattened.
- Add release automation if PyPI publication becomes part of the product.
- Expand security filtering if the current secret detector proves too narrow.
