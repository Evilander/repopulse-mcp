# Troubleshooting

## `repopulse search` says there is no index

Build the index first:

```bash
repopulse index --repo /abs/path/to/repo
```

Then confirm:

```bash
repopulse doctor --repo /abs/path/to/repo
```

## I get very few or zero files indexed

Check the repo-local config and excludes:

```toml
[indexer]
max_file_bytes = 2000000
extra_excludes = ["vendor/", "generated/"]
```

You can also override from the environment:

```bash
set REPOPULSE_MAX_FILE_BYTES=2000000
set REPOPULSE_EXTRA_EXCLUDES=vendor/,generated/
```

Then rerun `repopulse index`.

## `repopulse install` refuses to touch my config

That is intentional if the existing client config is malformed JSON or TOML. Fix the client config first, then rerun `repopulse install`.

RepoPulse now fails closed instead of overwriting broken config files.

## `read_file` rejects a file as sensitive

RepoPulse blocks files that look like private keys or obvious secret-bearing files. If the file is intentionally a fixture, rename it or move it outside the indexed repo during normal agent use. The guard is there to stop accidental secret exfiltration through MCP.

## Embeddings are not available

Install the embeddings extra:

```bash
pipx install 'git+https://github.com/evilander/repopulse-mcp.git#egg=repopulse-mcp[embeddings]'
```

Then enable it:

```bash
set REPOPULSE_EMBEDDINGS=1
```

If `sqlite-vec` or `fastembed` cannot initialize on your machine, RepoPulse falls back to FTS-only mode and logs the failure to stderr.

## MCP client says RepoPulse disconnected

Run the same server command directly and inspect stderr:

```bash
repopulse serve --repo /abs/path/to/repo
```

Common causes:

- The repo has not been indexed yet.
- The client config points at the wrong repo path.
- A repo-local config file is malformed.

## A search crashes with an FTS error

RepoPulse now raises explicit FTS errors instead of returning an empty result set for every failure. If this happens, the index is likely broken or the FTS table is missing. Reset and rebuild:

```bash
python -c "from pathlib import Path; from repopulse.indexer.run import reset_index; reset_index(Path('/abs/path/to/repo'))"
repopulse index --repo /abs/path/to/repo
```
