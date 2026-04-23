from __future__ import annotations

from pathlib import Path

import pytest

from repopulse.config import IndexerSettings
from repopulse.indexer.walker import walk


def test_walker_skips_gitignored(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("secrets/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "creds.py").write_text("API_KEY='x'\n", encoding="utf-8")

    paths = sorted(w.relative_posix for w in walk(tmp_path))
    assert "src/main.py" in paths
    assert not any("secrets/" in p for p in paths)


def test_walker_skips_default_excludes(tmp_path: Path) -> None:
    nm = tmp_path / "node_modules" / "x"
    nm.mkdir(parents=True)
    (nm / "a.js").write_text("1\n", encoding="utf-8")
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    paths = sorted(w.relative_posix for w in walk(tmp_path))
    assert paths == ["keep.py"]


def test_walker_skips_binaries(tmp_path: Path) -> None:
    (tmp_path / "real.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "bin.py").write_bytes(b"\x00\x01 binary pretending to be py \x00")
    paths = {w.relative_posix for w in walk(tmp_path)}
    assert "real.py" in paths
    assert "bin.py" not in paths


def test_walker_respects_size_limit(tmp_path: Path) -> None:
    big = tmp_path / "big.py"
    big.write_text("x" * 5_000_000, encoding="utf-8")
    tiny = tmp_path / "tiny.py"
    tiny.write_text("pass\n", encoding="utf-8")
    settings = IndexerSettings(max_file_bytes=1_000_000)
    paths = {w.relative_posix for w in walk(tmp_path, settings)}
    assert "tiny.py" in paths
    assert "big.py" not in paths


def test_walker_records_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "good.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "denied.py").write_text("print('nope')\n", encoding="utf-8")
    errors: list[str] = []
    original_open = Path.open

    def fake_open(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.name == "denied.py":
            raise PermissionError("blocked")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)
    paths = {w.relative_posix for w in walk(tmp_path, errors=errors)}
    assert "good.py" in paths
    assert "denied.py" not in paths
    assert any("denied.py: read failed" in error for error in errors)


def test_walker_skips_sensitive_content(tmp_path: Path) -> None:
    (tmp_path / "safe.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "keys.py").write_text(
        'private_key = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnop"\n',
        encoding="utf-8",
    )
    errors: list[str] = []
    paths = {w.relative_posix for w in walk(tmp_path, errors=errors)}
    assert "safe.py" in paths
    assert "keys.py" not in paths
    assert any("keys.py: skipped sensitive-looking content" in error for error in errors)
