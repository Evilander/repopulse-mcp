"""Locate repo roots and the .repopulse state directory."""

from __future__ import annotations

from pathlib import Path

REPOPULSE_DIR = ".repopulse"
INDEX_DB_NAME = "index.db"
CONFIG_FILE_NAME = "config.toml"


def find_repo_root(start: Path | None = None) -> Path:
    """Walk upward from `start` looking for a repository marker.

    Preference order: `.repopulse`, `.git`, `pyproject.toml`, `package.json`.
    Falls back to `start` (or cwd) when nothing is found so this tool still
    works in plain directories.
    """
    start = (start or Path.cwd()).resolve()
    markers = (REPOPULSE_DIR, ".git", "pyproject.toml", "package.json")
    for directory in (start, *start.parents):
        for marker in markers:
            if (directory / marker).exists():
                return directory
    return start


def state_dir(repo_root: Path) -> Path:
    return repo_root / REPOPULSE_DIR


def index_db_path(repo_root: Path) -> Path:
    return state_dir(repo_root) / INDEX_DB_NAME


def ensure_state_dir(repo_root: Path) -> Path:
    directory = state_dir(repo_root)
    directory.mkdir(parents=True, exist_ok=True)
    gitignore = directory / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    return directory


def relative_posix(path: Path, root: Path) -> str:
    """Return `path` expressed relative to `root` using POSIX separators."""
    rel = path.resolve().relative_to(root.resolve())
    return rel.as_posix()
