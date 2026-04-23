from __future__ import annotations

from pathlib import Path

from repopulse.retrieval.dense import DenseResult
from repopulse.retrieval.hybrid import search


def test_search_returns_rate_limit_chunks(indexed_repo: Path, store) -> None:  # type: ignore[no-untyped-def]
    response = search(store, "rate limit throttle", limit=5)
    assert response.results, "expected at least one match"
    # The throttle.py file should surface.
    paths = [r.path for r in response.results]
    assert any("throttle.py" in p for p in paths)
    assert response.trace_id.startswith("rp_")
    assert response.duration_ms >= 0


def test_search_finds_user_service(indexed_repo: Path, store) -> None:  # type: ignore[no-untyped-def]
    response = search(store, "user service typescript", limit=5)
    assert response.results
    paths = [r.path for r in response.results]
    assert any(p.endswith(".ts") for p in paths)


def test_search_persists_trace(indexed_repo: Path, store) -> None:  # type: ignore[no-untyped-def]
    response = search(store, "token bucket", limit=3)
    record = store.traces.get_trace(response.trace_id)
    assert record is not None
    assert record["trace"]["query"] == "token bucket"
    assert record["items"], "trace items must be recorded"
    stage_names = [stage["name"] for stage in record["stages"]]
    assert stage_names == [
        "normalize_query",
        "fts_candidates",
        "dense_candidates",
        "rrf_fuse",
        "graph_expand",
        "finalize",
    ]
    assert record["trace"]["notes"]["pipeline_version"] == 1


def test_search_empty_query(indexed_repo: Path, store) -> None:  # type: ignore[no-untyped-def]
    response = search(store, "", limit=5)
    # Without a dense backend, an empty query must produce no results and no
    # modes — it is a degenerate call, not a wildcard.
    assert response.results == []
    assert response.modes_used == []
    assert response.sufficiency == 0.0


def test_fts_mode_only(indexed_repo: Path, store) -> None:  # type: ignore[no-untyped-def]
    response = search(store, "throttle", limit=3, mode="fts")
    assert "fts" in response.modes_used
    assert "dense" not in response.modes_used
    assert response.results, "expected at least one FTS hit for 'throttle'"


def test_search_components_carry_fts_rank(indexed_repo: Path, store) -> None:  # type: ignore[no-untyped-def]
    """Every returned result must have a score_components dict with fts_rank populated (since we have no dense backend in tests)."""
    response = search(store, "rate limiting", limit=5, mode="fts")
    for result in response.results:
        assert "fts_rank" in result.components
        # fts_rank may be None for pure graph-expanded neighbors, but at least
        # one direct hit must have it.
    direct_hits = [r for r in response.results if r.components.get("fts_rank") is not None]
    assert direct_hits, "every non-graph result needs an fts_rank component"


def test_search_candidates_always_gte_returned(indexed_repo: Path, store) -> None:  # type: ignore[no-untyped-def]
    response = search(store, "throttle", limit=2)
    assert response.candidates_considered >= len(response.results)


class _RecordingDenseBackend:
    available = True

    def __init__(self) -> None:
        self.ensure_calls: list[list[int]] = []
        self.search_calls: list[list[int]] = []

    def ensure_embeddings(self, items: list[tuple[int, str]]) -> int:
        self.ensure_calls.append([chunk_id for chunk_id, _ in items])
        return len(items)

    def search(
        self, query_text: str, candidate_chunk_ids: list[int], limit: int = 50
    ) -> list[DenseResult]:
        self.search_calls.append(candidate_chunk_ids)
        ranked = list(reversed(candidate_chunk_ids))[:limit]
        return [
            DenseResult(chunk_id=chunk_id, distance=float(rank))
            for rank, chunk_id in enumerate(ranked, start=1)
        ]


def test_dense_mode_reranks_fts_candidate_pool(indexed_repo: Path, store) -> None:  # type: ignore[no-untyped-def]
    backend = _RecordingDenseBackend()
    expected_pool = [
        int(row["chunk_id"])
        for row in store.fts_search("throttle", limit=500)
    ]
    response = search(store, "throttle", limit=3, mode="dense", dense_backend=backend)
    assert backend.ensure_calls == [expected_pool]
    assert backend.search_calls == [expected_pool]
    assert response.results
    assert response.modes_used == ["fts", "dense"]
    assert response.results[0].chunk_id == expected_pool[-1]


def test_dense_mode_without_backend_returns_no_results(indexed_repo: Path, store) -> None:  # type: ignore[no-untyped-def]
    response = search(store, "throttle", limit=3, mode="dense", dense_backend=None)
    assert response.results == []
    assert response.modes_used == ["fts"]
