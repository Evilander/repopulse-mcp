"""Auto-detect coding-agent configs and (optionally) inject RepoPulse.

Supports:
  * Claude Desktop (`claude_desktop_config.json`)
  * Claude Code (`~/.claude.json` / `<repo>/.claude/mcp.json`)
  * Cursor (`~/.cursor/mcp.json`)
  * Continue (`~/.continue/config.json`)
  * OpenAI Codex CLI (`~/.codex/config.toml`)

We write conservatively: if an entry named `repopulse` already exists we
replace it; any siblings are preserved. We never delete or rewrite unrelated
MCP servers.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class AgentTarget:
    name: str
    path: Path
    format: str  # "json" or "toml-codex"


def candidate_targets() -> list[AgentTarget]:
    home = Path.home()
    candidates: list[AgentTarget] = []

    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(
                AgentTarget(
                    "Claude Desktop",
                    Path(appdata) / "Claude" / "claude_desktop_config.json",
                    "json",
                )
            )
    elif sys.platform == "darwin":
        candidates.append(
            AgentTarget(
                "Claude Desktop",
                home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
                "json",
            )
        )
    else:
        candidates.append(
            AgentTarget(
                "Claude Desktop",
                home / ".config" / "Claude" / "claude_desktop_config.json",
                "json",
            )
        )

    candidates.extend(
        [
            AgentTarget("Claude Code (user)", home / ".claude.json", "json"),
            AgentTarget("Cursor", home / ".cursor" / "mcp.json", "json"),
            AgentTarget("Continue", home / ".continue" / "config.json", "json"),
            AgentTarget("OpenAI Codex", home / ".codex" / "config.toml", "toml-codex"),
        ]
    )
    return candidates


def _repopulse_command(repo_root: Path) -> tuple[str, list[str]]:
    binary = shutil.which("repopulse")
    if binary:
        return binary, ["serve", "--repo", str(repo_root)]
    # Fallback: `python -m repopulse ...`
    python = shutil.which("python") or shutil.which("python3") or sys.executable
    return python, ["-m", "repopulse", "serve", "--repo", str(repo_root)]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} contains invalid JSON; refusing to overwrite it") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a top-level JSON object")
    return payload


def _backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return path.with_name(f"{path.name}.repopulse-backup-{stamp}")


def _atomic_write_text(path: Path, content: str) -> None:
    """Write `content` to `path` via temp-file + rename.

    Protects existing configs from partial writes when the process is killed
    mid-write. Also preserves a timestamped backup of the prior contents.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # Non-fatal: we'd rather lose the backup than refuse the install.
        with contextlib.suppress(OSError):
            shutil.copy2(path, _backup_path(path))
    fd, tmp_str = tempfile.mkstemp(dir=str(path.parent), prefix=".repopulse_tmp_")
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _write_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(data, indent=2) + "\n")


def _entry_json(repo_root: Path) -> dict[str, Any]:
    binary, args = _repopulse_command(repo_root)
    return {
        "command": binary,
        "args": args,
        "env": {},
    }


def install_json_target(target: AgentTarget, repo_root: Path) -> None:
    data = _load_json(target.path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers["repopulse"] = _entry_json(repo_root)
    data["mcpServers"] = servers
    _write_json(target.path, data)


def install_codex_target(target: AgentTarget, repo_root: Path) -> None:
    """OpenAI Codex CLI uses `~/.codex/config.toml`."""
    binary, args = _repopulse_command(repo_root)
    existing = ""
    if target.path.exists():
        try:
            existing = target.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OSError(f"Could not read existing Codex config at {target.path}: {exc}") from exc
        if existing.strip():
            try:
                tomllib.loads(existing)
            except tomllib.TOMLDecodeError as exc:
                raise ValueError(f"{target.path} contains invalid TOML; refusing to overwrite it") from exc
    # Very light TOML write: we append/replace a named table. We avoid a full
    # TOML round-trip to keep deps small; users can edit the file later.
    marker = "[mcp_servers.repopulse]"
    new_block = "\n".join(
        [
            marker,
            f'command = "{_escape_toml(binary)}"',
            "args = [" + ", ".join(f'"{_escape_toml(a)}"' for a in args) + "]",
        ]
    )
    if marker in existing:
        # Replace existing block until the next blank line or another section.
        lines = existing.splitlines()
        out: list[str] = []
        skipping = False
        replaced = False
        for line in lines:
            if line.strip() == marker and not replaced:
                out.append(new_block)
                skipping = True
                replaced = True
                continue
            if skipping:
                if line.startswith("[") or line.strip() == "":
                    skipping = False
                    out.append(line)
                continue
            out.append(line)
        _atomic_write_text(target.path, "\n".join(out).rstrip() + "\n")
        return
    joiner = "\n\n" if existing and not existing.endswith("\n\n") else ""
    _atomic_write_text(target.path, existing + joiner + new_block + "\n")


def _escape_toml(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def install_target(target: AgentTarget, repo_root: Path) -> None:
    if target.format == "json":
        install_json_target(target, repo_root)
    elif target.format == "toml-codex":
        install_codex_target(target, repo_root)
    else:
        raise ValueError(f"Unknown target format: {target.format}")
