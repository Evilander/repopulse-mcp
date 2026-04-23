# RepoPulse MCP

RepoPulse MCP is a local-first code retrieval server for coding agents.

It indexes a repository into SQLite, exposes retrieval tools over MCP stdio, and records a replayable trace for every search or file-context call. The point is not just "better search"; it is visible retrieval provenance. When an agent misses the right file, you can inspect the trace instead of guessing whether the model hallucinated or retrieval failed.

## Short Pitch

- Local only. No hosted index, no cloud requirement.
- Built for Claude Code, Cursor, Continue, Codex CLI, and similar MCP clients.
- Retrieval trace is a product feature, not debug exhaust.
- Safe by default: repo-relative reads only, secret-looking files skipped, invalid client configs not overwritten.

## Scope Today

- Python CLI and MCP server
- SQLite + FTS5 store
- Optional local dense reranking
- Symbol lookup, reference lookup, file reads, trace replay, and bad-trace feedback

## Not In Scope Yet

- Hosted service
- HTTP transport
- Background indexing jobs
- Web trace UI
- PyPI release workflow
