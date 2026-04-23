"""End-to-end story tests — the README walkthrough.

These tests exercise the product pitch: index, search, trace, diagnose.
If any of these break, the README example is lying.
"""

from __future__ import annotations

from pathlib import Path

from conftest import invoke_tool

from repopulse.indexer.run import index_repo
from repopulse.mcp_server.server import build_server


def test_full_story_index_search_trace_diagnose(tiny_repo: Path) -> None:
    # 1. Index.
    stats = index_repo(tiny_repo)
    assert stats.files_indexed >= 3
    assert stats.symbols_written > 0

    # 2. Build the MCP server (as the agent would connect to).
    server = build_server(tiny_repo)

    # 3. Search via the MCP tool surface — the agent's entry point.
    response = invoke_tool(server, "search_code", query="rate limiting token bucket", limit=5)
    assert response["trace_id"].startswith("rp_")
    assert response["results"], "story test: expected at least one hit"
    assert any("throttle" in r["path"] for r in response["results"])

    # 4. Replay the trace. Every returned result must be represented.
    trace = invoke_tool(server, "get_context_trace", trace_id=response["trace_id"])
    returned_items = [i for i in trace["items"] if i["returned"]]
    assert len(returned_items) == len(response["results"])
    assert [stage["name"] for stage in trace["stages"]] == [
        "normalize_query",
        "fts_candidates",
        "dense_candidates",
        "rrf_fuse",
        "graph_expand",
        "finalize",
    ]

    # 5. Diagnose. Must return hints + candidate_symbols for identifiers
    # that exist in the index.
    diag = invoke_tool(server, "diagnose_missing_context", query="TokenBucket rate")
    tokens_with_matches = [s["token"] for s in diag["candidate_symbols"]]
    assert "TokenBucket" in tokens_with_matches

    # 6. find_symbol with `match=exact` must find TokenBucket precisely.
    lookup = invoke_tool(server, "find_symbol", name="TokenBucket", match="exact")
    exact_names = [m["name"] for m in lookup["matches"]]
    assert exact_names == ["TokenBucket"]

    # 7. find_symbol with `match=prefix` finds TokenBucket and anything
    #    whose qualified_name begins with Token (e.g. TokenBucket.allow).
    prefix = invoke_tool(server, "find_symbol", name="Token", match="prefix")
    assert prefix["matches"], "prefix match on 'Token' should find TokenBucket"
    for m in prefix["matches"]:
        assert (
            m["name"].lower().startswith("token")
            or m["qualified_name"].lower().startswith("token")
        )

    # 8. explain_last_result is a shortcut that returns the most recent trace.
    last = invoke_tool(server, "explain_last_result")
    assert last["trace"] is not None

    # 9. Health check.
    health = invoke_tool(server, "get_index_health")
    assert health["healthy"] is True
    assert health["files"] >= 3

    # 10. Every context-returning tool writes a trace — not just search.
    assert "trace_id" in lookup
    find_ref = invoke_tool(server, "find_references", name="throttle")
    assert "trace_id" in find_ref
    read_resp = invoke_tool(server, "read_file", path="src/throttle.py", end_line=5)
    assert "trace_id" in read_resp

    # 11. Bad-trace feedback loop: the user's only corrective channel.
    flag = invoke_tool(server, "mark_bad_trace", trace_id=response["trace_id"], reason="missed redis backend")
    assert flag["updated"] is True
    bad = invoke_tool(server, "list_bad_traces")
    assert bad["count"] >= 1
    assert any(r["id"] == response["trace_id"] for r in bad["bad_traces"])
