# Wiring RepoPulse into MCP clients

`repopulse install` autodetects every client listed below. If you prefer to edit configs by hand, these are the canonical snippets.

All of them launch `repopulse serve --repo /abs/path/to/your/repo` over stdio. Use an absolute path — coding agents launch the server from their own CWD, not yours.

## Claude Desktop

`~/.config/Claude/claude_desktop_config.json` (Linux), `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS), or `%APPDATA%\Claude\claude_desktop_config.json` (Windows).

```json
{
  "mcpServers": {
    "repopulse": {
      "command": "repopulse",
      "args": ["serve", "--repo", "/abs/path/to/your/repo"]
    }
  }
}
```

## Claude Code

`~/.claude.json` — same shape as Claude Desktop.

## Cursor

`~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "repopulse": {
      "command": "repopulse",
      "args": ["serve", "--repo", "/abs/path/to/your/repo"]
    }
  }
}
```

## Continue (VS Code extension)

`~/.continue/config.json`:

```json
{
  "mcpServers": {
    "repopulse": {
      "command": "repopulse",
      "args": ["serve", "--repo", "/abs/path/to/your/repo"]
    }
  }
}
```

## OpenAI Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.repopulse]
command = "repopulse"
args = ["serve", "--repo", "/abs/path/to/your/repo"]
```

## Verifying the connection

After wiring up, ask your coding agent to call the `get_index_health` tool. You should see file/chunk/symbol counts. If nothing comes back, the MCP server probably failed to start — run `repopulse serve --repo /abs/path/to/your/repo` in a terminal and look at stderr.

## Running with local embeddings

Set `REPOPULSE_EMBEDDINGS=1` in the `env` block:

```json
{
  "mcpServers": {
    "repopulse": {
      "command": "repopulse",
      "args": ["serve", "--repo", "/abs/path/to/your/repo"],
      "env": { "REPOPULSE_EMBEDDINGS": "1" }
    }
  }
}
```

This requires the `[embeddings]` extra installed (`pipx install 'git+https://github.com/evilander/repopulse-mcp.git#egg=repopulse-mcp[embeddings]'`). The first call downloads the model (~100 MB); subsequent calls are cached.

## Multiple repos

Register the server once per repo. Give each a distinct MCP server name:

```json
{
  "mcpServers": {
    "repopulse-api":    { "command": "repopulse", "args": ["serve", "--repo", "/code/api"] },
    "repopulse-web":    { "command": "repopulse", "args": ["serve", "--repo", "/code/web"] },
    "repopulse-infra":  { "command": "repopulse", "args": ["serve", "--repo", "/code/infra"] }
  }
}
```
