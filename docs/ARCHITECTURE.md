# RepoPulse MCP — Architecture

## Thesis

Every other code-context MCP server in April 2026 is a black box. You ask, it answers, you trust. When the answer is wrong you have no idea whether the model hallucinated, whether retrieval missed the right file, or whether the right file was never indexed.

**RepoPulse is the indexer that shows its work.** Every retrieval call produces a *trace* — a persisted record of what was scored, why, and what was returned. When the assistant writes bad code, you replay the trace and see exactly where context failed.

## Non-goals for v1

- Cloud hosting, multi-user, RBAC.
- LSP integration (Serena already owns that lane).
- Real-time file-watcher (v1 is pull-based: `refresh_index`).
- Symbol editing (Serena again).
- Web UI / Tauri desktop shell. The trace is consumed via CLI (`repopulse trace <id>`) and via MCP tools. A web UI is post-MVP.
- GPU. Everything must run on a developer laptop CPU.

## Layered design

```
+------------------------------------------------------------+
|  CLI (typer)                                               |
|    repopulse init | index | search | ask | trace | serve   |
|                   | install | doctor                       |
+------------------------------------------------------------+
|  MCP Server (mcp.server.Server, stdio)                     |
|    tools: index_repo, search_code, find_symbol,            |
|           find_references, read_file, get_context_trace,   |
|           explain_last_result, diagnose_missing_context,   |
|           get_index_health                                 |
+------------------------------------------------------------+
|  Retrieval                                                 |
|    hybrid(query) = rrf(fts5(query), dense(query))          |
|    + graph_expand(top_k)  (1-hop: imports, same-file)      |
|    + rank_cap(50)         (Drowning in Documents, 2024)    |
+------------------------------------------------------------+
|  Indexer                                                   |
|    walker (pathspec) -> language detect -> tree-sitter     |
|    -> symbols + chunks (cAST) -> SQLite(FTS5 + sqlite-vec) |
+------------------------------------------------------------+
|  Trace store                                               |
|    every retrieval logged with scores, timings, candidates |
|    replayable + queryable via get_context_trace            |
+------------------------------------------------------------+
|  Storage: SQLite at <repo>/.repopulse/index.db             |
|           (FTS5 + sqlite-vec + trace tables)               |
+------------------------------------------------------------+
```

## Data model

### `files`
- `id INTEGER PRIMARY KEY`
- `path TEXT UNIQUE NOT NULL` (relative to repo root, POSIX form)
- `language TEXT` (python, typescript, go, ...)
- `size_bytes INTEGER`
- `mtime_ns INTEGER`
- `content_hash TEXT` (blake2b-128 of content)
- `indexed_at INTEGER` (unix seconds)

### `chunks`
- `id INTEGER PRIMARY KEY`
- `file_id INTEGER REFERENCES files(id) ON DELETE CASCADE`
- `symbol_path TEXT` (dotted path: `ClassName.method_name` or empty for free-floating)
- `symbol_kind TEXT` (function, class, method, module, block)
- `start_line INTEGER` (1-indexed, inclusive)
- `end_line INTEGER` (1-indexed, inclusive)
- `byte_start INTEGER`
- `byte_end INTEGER`
- `breadcrumb TEXT` (prepended parent signatures for standalone readability)
- `text TEXT` (chunk content, may be truncated for storage)
- `token_estimate INTEGER`

### `chunks_fts` — FTS5 virtual table over `chunks.text + chunks.breadcrumb + symbol_path`
### `chunk_vectors` — sqlite-vec virtual table, one row per chunk (if embeddings enabled)

### `symbols`
- `id INTEGER PRIMARY KEY`
- `file_id INTEGER REFERENCES files(id) ON DELETE CASCADE`
- `name TEXT NOT NULL`
- `qualified_name TEXT` (dotted path)
- `kind TEXT NOT NULL`
- `line INTEGER NOT NULL`
- `col INTEGER`
- `signature TEXT`

### `references` (import edges + symbol-use edges)
- `id INTEGER PRIMARY KEY`
- `src_file_id INTEGER REFERENCES files(id) ON DELETE CASCADE`
- `src_line INTEGER`
- `ref_kind TEXT` (import, call, inherit)
- `target_name TEXT` (symbol name / module name)
- `target_file_id INTEGER` (null if unresolved)

### `traces`
- `id TEXT PRIMARY KEY` (short id: `rp_7fa3`)
- `created_at INTEGER`
- `query TEXT`
- `tool_name TEXT`
- `params_json TEXT`
- `duration_ms INTEGER`
- `result_count INTEGER`
- `sufficiency_score REAL`
- `notes_json TEXT`

### `trace_items`
- `trace_id TEXT REFERENCES traces(id) ON DELETE CASCADE`
- `rank INTEGER`
- `chunk_id INTEGER`
- `score REAL`
- `score_components_json TEXT` (`{"fts": 0.72, "dense": 0.84, "graph": 0.1}`)
- `returned BOOLEAN` (vs candidate-only)

## cAST chunking (simplified)

Based on Zhang et al. 2506.15655. Recursive AST-guided split with size bounds:

```
MIN_CHUNK_BYTES = 200
MAX_CHUNK_BYTES = 2000

def chunk(node, parents):
    if size(node) <= MAX_CHUNK_BYTES:
        emit(node, breadcrumb=signatures(parents))
        return
    if node has children of type {function, method, class, block}:
        for child in semantic_children(node):
            chunk(child, parents + [node])
    else:
        emit_sliced(node, breadcrumb=signatures(parents))

# After emit pass:
merge_adjacent_siblings_below_MIN_CHUNK_BYTES()
```

Breadcrumbs preserve context: a method chunk carries the class signature, file header, and any relevant decorators as a prepended comment block so it reads standalone.

## Retrieval pipeline

```
query
  -> normalize (lowercase identifiers split by camel/snake)
  -> parallel: fts5_search(query, limit=50)
             : dense_search(embed(query), limit=50)   [if embeddings on]
  -> RRF fuse (k=60)
  -> graph_expand(top_20, hops=1)      [add 1-hop neighbors with 0.5x weight]
  -> dedup + cap to 50
  -> optional rerank with cross-encoder (v1: skip)
  -> top_k return with per-chunk score_components
  -> log trace with all candidates, not just returned
```

RRF = reciprocal rank fusion, constant k=60: `score(c) = sum(1 / (k + rank_in_list_i))` across FTS and dense rankings.

## Context trace

Every tool call that returns context writes:
1. A `traces` row (the query, tool, params, timing, sufficiency score).
2. One `trace_items` row per *candidate considered* (not just returned) with component scores.

`get_context_trace(trace_id)` returns the full record. `explain_last_result()` returns the most recent trace for the current MCP session.

The sufficiency score is a heuristic in v1 (post-v1 upgrade: train a MiniLM classifier per Wen et al. 2411.06037):

```
def sufficiency(candidates, returned):
    if not returned: return 0.0
    top_score = max(c.score for c in returned)
    gap = top_score - median(c.score for c in returned)
    hit_count_factor = min(len(returned) / 5, 1.0)
    return min(top_score * 0.6 + gap * 0.2 + hit_count_factor * 0.2, 1.0)
```

Score >= 0.6 is "likely sufficient"; 0.3..0.6 "maybe"; <0.3 "probably missing something".

## `diagnose_missing_context`

Given the query and the trace, heuristically suggest what might be missing:
- Query mentions identifiers not present in any retrieved chunk -> "did you mean `<similar>`?" via name-similarity on the symbol table.
- Top score < 0.5 -> "low relevance; try adding terms or a path filter."
- Graph expansion produced high-scoring neighbors but they weren't returned -> "consider widening `limit`."
- No files of query's likely language (e.g., query mentions `.tsx` but no tsx files) -> "no matching files indexed; check .gitignore / excludes."

## Installation flow (copy the pattern from codebase-memory-mcp)

`repopulse install` detects:
- `~/.config/Claude/claude_desktop_config.json` (Claude Desktop macOS/Linux) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows)
- `~/.claude.json` (Claude Code project config)
- `~/.codex/config.toml` (OpenAI Codex CLI)
- `~/.cursor/mcp.json`
- `~/.continue/config.json`

For each found, prompt y/N, then inject an `mcpServers.repopulse` entry pointing at the current venv's `repopulse` binary.

## Package layout

```
repopulse-mcp/
├── pyproject.toml
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
├── docs/
│   ├── ARCHITECTURE.md        (this file)
│   ├── TROUBLESHOOTING.md
│   └── MCP_CLIENTS.md
├── src/repopulse/
│   ├── __init__.py
│   ├── __main__.py            (python -m repopulse)
│   ├── config.py              (pydantic Settings)
│   ├── logging.py             (structlog, stderr-only)
│   ├── paths.py               (locate repo root, .repopulse dir)
│   ├── languages.py           (ext -> language map + tree-sitter loader)
│   ├── indexer/
│   │   ├── __init__.py
│   │   ├── walker.py          (pathspec walker with default denies)
│   │   ├── chunker.py         (cAST)
│   │   ├── symbols.py         (tree-sitter queries)
│   │   ├── hasher.py          (blake2b content hash)
│   │   └── run.py             (pipeline orchestration)
│   ├── retrieval/
│   │   ├── __init__.py
│   │   ├── store.py           (SQLite + FTS5 + sqlite-vec)
│   │   ├── fts.py             (FTS5 query builder)
│   │   ├── dense.py           (fastembed wrapper, lazy-loaded)
│   │   ├── rrf.py             (rank fusion)
│   │   ├── graph.py           (import-based expansion)
│   │   └── hybrid.py          (top-level search)
│   ├── trace/
│   │   ├── __init__.py
│   │   ├── store.py           (trace persistence)
│   │   ├── ids.py             (short rp_xxxx ids)
│   │   └── sufficiency.py     (heuristic score)
│   ├── mcp_server/
│   │   ├── __init__.py
│   │   ├── server.py          (mcp.Server + stdio)
│   │   └── tools.py           (tool definitions)
│   └── cli/
│       ├── __init__.py
│       ├── app.py             (typer app)
│       ├── format.py          (rich tables, NO_COLOR aware)
│       └── install.py         (auto-config for MCP clients)
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   └── tiny_repo/         (minimal py + ts + go project)
│   ├── test_walker.py
│   ├── test_chunker.py
│   ├── test_symbols.py
│   ├── test_fts.py
│   ├── test_hybrid.py
│   ├── test_trace.py
│   ├── test_mcp_contract.py
│   └── test_cli_smoke.py
└── codex.md                   (handoff doc)
```

## Perf targets for v1

On a laptop (M1 Pro, 16GB, no GPU):
- Index a 1k-file JS/TS project (~80k LOC): < 30s end-to-end with embeddings off.
- Index same with embeddings on (fastembed bge-small): < 3 min cold (model download), < 90s warm.
- `search_code` P50 latency: < 50ms (FTS only), < 200ms (hybrid).
- `find_symbol` P50: < 10ms.
- Startup (CLI, no indexing): < 250ms.
- MCP stdio startup: < 500ms.

## Security posture

- Path confinement: all paths argued to MCP tools are resolved relative to the indexed repo root; any path that resolves outside is rejected with a clear error.
- No network by default. Embeddings are local (fastembed + ONNX). Explicit opt-in for any future remote model.
- Secrets: never embed `.env*`, `.pem`, `.key`, `*.p12` — in default deny list.
- SQL: parameterized everywhere. No string concatenation.
- FTS5 MATCH strings are built from tokenized user input (identifier-safe), never raw pasted SQL.
- Subprocess: none in v1 (no shelling out to ripgrep). All walking in pure Python.
