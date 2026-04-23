"""Walk a repo yielding candidate source files.

Rules:
  * Default deny list (see config.DEFAULT_EXCLUDES).
  * .gitignore at any level merged in (if present).
  * Files above `max_file_bytes` skipped.
  * Binary files (null byte in first 8 KB) skipped.
  * Secret-bearing files are skipped before indexing.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pathspec import GitIgnoreSpec

from repopulse.config import DEFAULT_EXCLUDES, IndexerSettings
from repopulse.content_safety import detect_sensitive_content
from repopulse.languages import detect_language

_BINARY_SNIFF_BYTES = 8192


@dataclass(frozen=True)
class WalkedFile:
    path: Path
    relative_posix: str
    language: str | None
    size_bytes: int
    mtime_ns: int


def _build_spec(repo_root: Path, settings: IndexerSettings) -> GitIgnoreSpec:
    patterns: list[str] = list(DEFAULT_EXCLUDES)
    patterns.extend(settings.extra_excludes)
    if settings.respect_gitignore:
        gitignore = repo_root / ".gitignore"
        if gitignore.exists():
            with contextlib.suppress(OSError):
                patterns.extend(gitignore.read_text(encoding="utf-8").splitlines())
    return GitIgnoreSpec.from_lines(patterns)


def _record_error(errors: list[str] | None, where: str, detail: str) -> None:
    if errors is not None:
        errors.append(f"{where}: {detail}")


def _sniff(path: Path, *, relative_posix: str, errors: list[str] | None) -> bytes | None:
    try:
        with path.open("rb") as handle:
            return handle.read(_BINARY_SNIFF_BYTES)
    except OSError as exc:
        _record_error(errors, relative_posix, f"read failed: {exc}")
        return None


def walk(
    repo_root: Path,
    settings: IndexerSettings | None = None,
    *,
    errors: list[str] | None = None,
) -> Iterator[WalkedFile]:
    """Yield every source file eligible for indexing, ordered for determinism."""
    settings = settings or IndexerSettings()
    root = repo_root.resolve()
    spec = _build_spec(root, settings)

    seen_real: set[Path] = set()

    def _iter(directory: Path) -> Iterator[Path]:
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name)
        except (PermissionError, FileNotFoundError, OSError) as exc:
            where = "." if directory == root else directory.relative_to(root).as_posix()
            _record_error(errors, where, f"cannot list directory: {exc}")
            return
        for entry in entries:
            # Resolve every entry to its real path. A symlink that escapes the
            # repo (e.g. pointing at /etc/passwd or ~/.ssh/id_rsa) would have
            # its content indexed and served to the AI assistant otherwise.
            # For in-repo symlinks we use the *resolved* path so the actual
            # file is what gets indexed, and we dedupe against the resolved
            # identity to avoid double-indexing the same file.
            try:
                resolved = entry.resolve()
                rel = resolved.relative_to(root)
            except (ValueError, OSError) as exc:
                _record_error(errors, entry.name, f"cannot resolve path: {exc}")
                continue
            rel_str = rel.as_posix()
            if resolved.is_dir():
                if spec.match_file(rel_str + "/"):
                    continue
                if resolved in seen_real:
                    continue
                seen_real.add(resolved)
                yield from _iter(resolved)
            elif resolved.is_file():
                if spec.match_file(rel_str):
                    continue
                if resolved in seen_real:
                    continue
                seen_real.add(resolved)
                yield resolved

    for path in _iter(root):
        rel = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
        except OSError as exc:
            _record_error(errors, rel, f"cannot stat file: {exc}")
            continue
        if stat.st_size > settings.max_file_bytes:
            continue
        language = detect_language(path)
        if language is None:
            continue
        sniff = _sniff(path, relative_posix=rel, errors=errors)
        if sniff is None:
            continue
        if b"\x00" in sniff:
            continue
        if reason := detect_sensitive_content(sniff):
            _record_error(errors, rel, f"skipped sensitive-looking content ({reason})")
            continue
        yield WalkedFile(
            path=path,
            relative_posix=rel,
            language=language,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
