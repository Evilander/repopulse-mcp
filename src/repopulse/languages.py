"""Language detection and tree-sitter loader."""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".scala": "scala",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".m": "objc",
    ".lua": "lua",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".json": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".markdown": "markdown",
}

# Languages we have specialized symbol queries for. Others still get chunked,
# just without symbol extraction.
SYMBOL_SUPPORTED: frozenset[str] = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "tsx",
        "go",
        "rust",
        "java",
        "ruby",
        "c",
        "cpp",
    }
)


@dataclass(frozen=True)
class LanguageInfo:
    name: str
    can_parse: bool
    can_extract_symbols: bool


def detect_language(path: Path) -> str | None:
    """Return language name for `path` or None if unknown/binary."""
    suffix = path.suffix.lower()
    if suffix in EXT_TO_LANGUAGE:
        return EXT_TO_LANGUAGE[suffix]
    name = path.name.lower()
    if name in {"makefile", "dockerfile"}:
        return "make" if "make" in name else "dockerfile"
    return None


@functools.cache
def _get_parser(language: str):  # type: ignore[no-untyped-def]
    """Return a tree-sitter Parser loaded for `language`, or None if unavailable."""
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:  # pragma: no cover - declared in deps
        return None
    try:
        return cast(Any, get_parser)(language)
    except Exception:
        return None


def parse(text: bytes, language: str):  # type: ignore[no-untyped-def]
    """Parse `text` and return a tree-sitter Tree, or None if language unavailable."""
    parser = _get_parser(language)
    if parser is None:
        return None
    return parser.parse(text)


def language_info(language: str) -> LanguageInfo:
    can_parse = _get_parser(language) is not None
    return LanguageInfo(
        name=language,
        can_parse=can_parse,
        can_extract_symbols=can_parse and language in SYMBOL_SUPPORTED,
    )
