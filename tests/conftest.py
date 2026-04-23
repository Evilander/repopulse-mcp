"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from repopulse.config import IndexerSettings, Settings
from repopulse.indexer.run import index_repo
from repopulse.paths import ensure_state_dir, index_db_path
from repopulse.retrieval.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def tiny_repo(tmp_path: Path) -> Path:
    """Copy the tiny_repo fixture to a fresh tmp dir and return its path."""
    import shutil

    src = FIXTURES / "tiny_repo"
    dst = tmp_path / "tiny_repo"
    shutil.copytree(src, dst)
    return dst


@pytest.fixture
def indexed_repo(tiny_repo: Path) -> Path:
    """Return a tiny repo with the index already built."""
    ensure_state_dir(tiny_repo)
    settings = Settings(indexer=IndexerSettings())
    index_repo(tiny_repo, settings=settings)
    return tiny_repo


@pytest.fixture
def store(indexed_repo: Path):  # type: ignore[no-untyped-def]
    s = Store(index_db_path(indexed_repo))
    try:
        yield s
    finally:
        s.close()


def invoke_tool(server, tool_name: str, **kwargs):  # type: ignore[no-untyped-def]
    tool = server._tool_manager._tools[tool_name]
    fn = getattr(tool, "fn", None) or getattr(tool, "func", None)
    if fn is None:
        raise RuntimeError(f"Tool {tool_name!r} has no callable function attribute")
    return fn(**kwargs)
