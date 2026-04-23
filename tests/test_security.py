"""Security regression tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import invoke_tool

from repopulse.config import IndexerSettings
from repopulse.indexer.run import index_repo
from repopulse.indexer.walker import walk
from repopulse.mcp_server.server import build_server
from repopulse.retrieval.fts import MAX_IDENT_TOKENS, build_match_string


def test_walker_rejects_escaping_symlink(tmp_path: Path) -> None:
    """A symlink that escapes the repo must not be followed."""
    import os
    import sys

    if sys.platform.startswith("win"):
        # Symlinks on Windows require admin; the test isn't load-bearing here
        # because Path.resolve() on non-admin Windows usually fails silently
        # too. Skip rather than produce a flaky result.
        pytest.skip("symlink creation requires admin on Windows")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_secret = outside / "secret.py"
    outside_secret.write_text("API_KEY = 'leaked'\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "visible.py").write_text("x = 1\n", encoding="utf-8")
    # Symlink inside the repo pointing OUT of it.
    escape = repo / "escape.py"
    try:
        os.symlink(outside_secret, escape)
    except OSError as exc:
        pytest.skip(f"cannot create symlink: {exc}")
    paths = {w.relative_posix for w in walk(repo, IndexerSettings())}
    assert "visible.py" in paths
    assert "escape.py" not in paths


def test_fts_query_caps_tokens() -> None:
    # Feeding a huge number of identifier-ish tokens must not produce an
    # unbounded MATCH string (would stall the FTS5 scan on huge repos).
    q = " ".join(f"tok{i}" for i in range(200))
    match = build_match_string(q)
    # Count OR groups — one per token (each group is (...)).
    or_groups = match.count(" OR ((")
    # Some allowance for lead-in; upper bound is MAX_IDENT_TOKENS.
    assert or_groups + 1 <= MAX_IDENT_TOKENS


def test_fts_query_clips_enormous_input() -> None:
    # A 1 MB blob of junk must not cause exponential regex work.
    big = "identifier_word " * 200_000
    match = build_match_string(big)
    # At most MAX_IDENT_TOKENS tokens get through.
    assert match.count(" OR ((") + 1 <= 32


def test_read_file_blocks_sensitive_content(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "visible.py").write_text("print('ok')\n", encoding="utf-8")
    (repo / "secret.py").write_text(
        'private_key = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnop"\n',
        encoding="utf-8",
    )
    index_repo(repo)
    server = build_server(repo)
    with pytest.raises(PermissionError):
        invoke_tool(server, "read_file", path="secret.py")
