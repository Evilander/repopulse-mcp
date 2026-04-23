"""Exercise the MCP server's tool functions directly.

We don't spin up a real stdio transport here; instead we construct the server,
call each tool's underlying function, and assert the returned shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import invoke_tool


@pytest.fixture
def server(indexed_repo: Path):  # type: ignore[no-untyped-def]
    from repopulse.mcp_server.server import build_server

    return build_server(indexed_repo)


def test_mcp_lists_expected_tools(server) -> None:  # type: ignore[no-untyped-def]
    tool_names = set(server._tool_manager._tools)
    # Core + differentiators from the design doc.
    expected = {
        "index_repo",
        "search_code",
        "find_symbol",
        "find_references",
        "read_file",
        "get_context_trace",
        "explain_last_result",
        "diagnose_missing_context",
        "get_index_health",
        "list_indexed_files",
        "mark_bad_trace",
        "list_bad_traces",
    }
    missing = expected - tool_names
    assert not missing, f"MCP server missing tools: {missing}"


def test_mcp_search_code_shape(server) -> None:  # type: ignore[no-untyped-def]
    result = invoke_tool(server, "search_code", query="throttle rate limit", limit=3)
    assert "trace_id" in result
    assert "results" in result
    assert "sufficiency" in result
    assert isinstance(result["results"], list)


def test_mcp_find_symbol_shape(server) -> None:  # type: ignore[no-untyped-def]
    result = invoke_tool(server, "find_symbol", name="TokenBucket")
    assert "matches" in result
    names = [m["name"] for m in result["matches"]]
    assert "TokenBucket" in names


def test_mcp_read_file_rejects_traversal(server) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        invoke_tool(server, "read_file", path="../../../etc/passwd")


def test_mcp_read_file_rejects_absolute_path(server, indexed_repo: Path) -> None:  # type: ignore[no-untyped-def]
    absolute = str((indexed_repo / "src" / "throttle.py").resolve())
    with pytest.raises(ValueError):
        invoke_tool(server, "read_file", path=absolute)


def test_mcp_read_file_happy(server) -> None:  # type: ignore[no-untyped-def]
    result = invoke_tool(server, "read_file", path="src/throttle.py", start_line=1, end_line=10)
    assert "content" in result
    assert result["start_line"] == 1
    assert result["total_lines"] > 0


def test_mcp_health(server) -> None:  # type: ignore[no-untyped-def]
    result = invoke_tool(server, "get_index_health")
    assert result["files"] >= 3
    assert result["chunks"] > 0
    assert result["healthy"] is True


def test_mcp_explain_returns_trace_or_note(server) -> None:  # type: ignore[no-untyped-def]
    # First call -- no trace yet, should return the empty note.
    initial = invoke_tool(server, "explain_last_result")
    assert "trace" in initial or "items" in initial

    # After a search, a trace should exist.
    invoke_tool(server, "search_code", query="healthcheck", limit=2)
    after = invoke_tool(server, "explain_last_result")
    assert "trace" in after
    assert after["trace"] is not None


def test_mcp_diagnose_suggests_similar(server) -> None:  # type: ignore[no-untyped-def]
    invoke_tool(server, "search_code", query="TokenBuket", limit=5)  # typo intentional
    diag = invoke_tool(server, "diagnose_missing_context", query="TokenBuket")
    # Should at least return a hints list.
    assert "hints" in diag
