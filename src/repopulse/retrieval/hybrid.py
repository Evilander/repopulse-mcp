"""Top-level search: explicit retrieval stages + persisted pipeline trace."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from repopulse.config import RetrievalSettings
from repopulse.retrieval import fts as fts_mod
from repopulse.retrieval import rrf as rrf_mod
from repopulse.retrieval.dense import DenseBackend
from repopulse.retrieval.graph import expand_neighbors, hydrate_chunks
from repopulse.retrieval.store import Store
from repopulse.trace import ids as trace_ids
from repopulse.trace.sufficiency import sufficiency_score


@dataclass
class SearchResult:
    chunk_id: int
    path: str
    language: str | None
    symbol_path: str
    symbol_kind: str
    start_line: int
    end_line: int
    breadcrumb: str
    text: str
    score: float
    components: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResponse:
    trace_id: str
    query: str
    results: list[SearchResult]
    candidates_considered: int
    duration_ms: int
    sufficiency: float
    modes_used: list[str]


@dataclass
class CandidateState:
    chunk_id: int
    fts_rank: int | None = None
    dense_rank: int | None = None
    graph_reason: str | None = None
    fused_score: float = 0.0
    normalized_score: float = 0.0
    final_score: float = 0.0
    returned: bool = False

    def components(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "fts_rank": self.fts_rank,
            "dense_rank": self.dense_rank,
            "fused_score": round(self.fused_score, 6),
        }
        if self.graph_reason:
            payload["graph_reason"] = self.graph_reason
        return payload


@dataclass
class StageRecord:
    name: str
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs_per_chunk: dict[str, float] = field(default_factory=dict)
    timings_ms: int = 0
    params: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "inputs": self.inputs,
            "outputs_per_chunk": self.outputs_per_chunk,
            "timings_ms": self.timings_ms,
            "params": self.params,
        }
        if self.summary:
            payload["summary"] = self.summary
        return payload


class Stage(Protocol):
    name: str

    def run(self, ctx: RetrievalContext) -> None: ...


@dataclass
class RetrievalContext:
    query: str
    mode: str
    limit: int
    settings: RetrievalSettings
    stages: list[StageRecord] = field(default_factory=list)
    candidates: dict[int, CandidateState] = field(default_factory=dict)
    modes_used: list[str] = field(default_factory=list)
    match_query: str = ""
    fts_rows: list[dict[str, Any]] = field(default_factory=list)
    dense_rows: list[tuple[int, float]] = field(default_factory=list)
    fused: list[tuple[int, float, dict[int, int]]] = field(default_factory=list)
    base_ranked: list[int] = field(default_factory=list)
    neighbors: list[tuple[int, str]] = field(default_factory=list)
    combined_ids: list[int] = field(default_factory=list)
    results: list[SearchResult] = field(default_factory=list)

    @property
    def candidate_limit(self) -> int:
        return self.settings.candidate_limit

    def ensure_candidate(self, chunk_id: int) -> CandidateState:
        return self.candidates.setdefault(chunk_id, CandidateState(chunk_id=chunk_id))


def search(
    store: Store,
    query: str,
    *,
    limit: int = 10,
    mode: str = "hybrid",
    settings: RetrievalSettings | None = None,
    dense_backend: DenseBackend | None = None,
) -> SearchResponse:
    """Execute a hybrid search and persist a trace.

    `mode` in {"hybrid", "fts", "dense"}.
    Dense mode reranks an FTS candidate pool; it does not run a whole-corpus
    vector search.
    """
    t0 = time.perf_counter()
    ctx = RetrievalContext(
        query=query,
        mode=mode,
        limit=limit,
        settings=settings or RetrievalSettings(),
    )

    pipeline: list[Stage] = [
        NormalizeQuery(),
        FTSCandidates(store),
        DenseCandidates(dense_backend),
        RRFFuse(),
        GraphExpand(store),
        Finalize(store),
    ]
    for stage in pipeline:
        stage.run(ctx)

    returned = ctx.results[:limit]
    duration_ms = _elapsed_ms(t0)
    returned_scores = [result.score for result in returned]
    sufficiency = sufficiency_score(returned_scores, len(ctx.results))

    trace_id = trace_ids.new_id()
    _persist_trace(
        store=store,
        trace_id=trace_id,
        ctx=ctx,
        duration_ms=duration_ms,
        returned_count=len(returned),
        sufficiency=sufficiency,
    )

    return SearchResponse(
        trace_id=trace_id,
        query=query,
        results=returned,
        candidates_considered=len(ctx.results),
        duration_ms=duration_ms,
        sufficiency=sufficiency,
        modes_used=ctx.modes_used,
    )


class NormalizeQuery:
    name = "normalize_query"

    def run(self, ctx: RetrievalContext) -> None:
        started = time.perf_counter()
        ctx.match_query = fts_mod.build_match_string(ctx.query)
        ctx.stages.append(
            StageRecord(
                name=self.name,
                inputs={"query": ctx.query},
                timings_ms=_elapsed_ms(started),
                params={"mode": ctx.mode},
                summary={
                    "match_query": ctx.match_query,
                    "has_query_terms": bool(ctx.match_query),
                },
            )
        )


@dataclass
class FTSCandidates:
    store: Store
    name: str = "fts_candidates"

    def run(self, ctx: RetrievalContext) -> None:
        started = time.perf_counter()
        reason: str | None = None
        rows: list[dict[str, Any]] = []
        if ctx.mode not in {"hybrid", "fts", "dense"}:
            reason = "mode_disabled"
        elif not ctx.match_query:
            reason = "empty_query"
        else:
            rows = self.store.fts_search(ctx.match_query, limit=ctx.candidate_limit)
            if rows:
                ctx.modes_used.append("fts")
            for rank, row in enumerate(rows, start=1):
                candidate = ctx.ensure_candidate(int(row["chunk_id"]))
                candidate.fts_rank = rank
        ctx.fts_rows = rows
        ctx.stages.append(
            StageRecord(
                name=self.name,
                inputs={"match_query": ctx.match_query},
                outputs_per_chunk={
                    str(row["chunk_id"]): float(rank)
                    for rank, row in enumerate(rows, start=1)
                },
                timings_ms=_elapsed_ms(started),
                params={"limit": ctx.candidate_limit},
                summary={
                    "hit_count": len(rows),
                    "reason": reason,
                },
            )
        )


@dataclass
class DenseCandidates:
    dense_backend: DenseBackend | None
    name: str = "dense_candidates"

    def run(self, ctx: RetrievalContext) -> None:
        started = time.perf_counter()
        reason: str | None = None
        rows: list[tuple[int, float]] = []
        embedded_count = 0
        if ctx.mode not in {"hybrid", "dense"}:
            reason = "mode_disabled"
        elif self.dense_backend is None or not self.dense_backend.available:
            reason = "backend_unavailable"
        elif not ctx.fts_rows:
            reason = "no_fts_candidate_pool"
        else:
            candidate_items = [
                (int(row["chunk_id"]), _embedding_text(row))
                for row in ctx.fts_rows
            ]
            embedded_count = self.dense_backend.ensure_embeddings(candidate_items)
            dense_results = self.dense_backend.search(
                ctx.query,
                [chunk_id for chunk_id, _text in candidate_items],
                limit=ctx.candidate_limit,
            )
            rows = [(result.chunk_id, result.distance) for result in dense_results]
            if rows:
                ctx.modes_used.append("dense")
            for rank, (chunk_id, _distance) in enumerate(rows, start=1):
                candidate = ctx.ensure_candidate(chunk_id)
                candidate.dense_rank = rank
        ctx.dense_rows = rows
        ctx.stages.append(
            StageRecord(
                name=self.name,
                inputs={"query": ctx.query},
                outputs_per_chunk={
                    str(chunk_id): float(rank)
                    for rank, (chunk_id, _distance) in enumerate(rows, start=1)
                },
                timings_ms=_elapsed_ms(started),
                params={"limit": ctx.candidate_limit},
                summary={
                    "hit_count": len(rows),
                    "embedded_count": embedded_count,
                    "candidate_pool_count": len(ctx.fts_rows),
                    "reason": reason,
                },
            )
        )


class RRFFuse:
    name = "rrf_fuse"

    def run(self, ctx: RetrievalContext) -> None:
        started = time.perf_counter()
        fts_ranked = [int(row["chunk_id"]) for row in ctx.fts_rows]
        dense_ranked = [chunk_id for chunk_id, _distance in ctx.dense_rows]
        if ctx.mode == "dense":
            ranked_lists = [dense_ranked] if dense_ranked else []
        elif ctx.mode == "fts":
            ranked_lists = [fts_ranked] if fts_ranked else []
        else:
            ranked_lists = [ranked for ranked in (fts_ranked, dense_ranked) if ranked]
        ctx.fused = rrf_mod.fuse(ranked_lists, k=ctx.settings.rrf_k)
        normalized = rrf_mod.normalized_scores(ctx.fused)
        ctx.base_ranked = [chunk_id for chunk_id, _score, _ranks in ctx.fused][
            : ctx.candidate_limit
        ]
        for chunk_id, fused_score, _ranks in ctx.fused:
            candidate = ctx.ensure_candidate(chunk_id)
            candidate.fused_score = float(fused_score)
            candidate.normalized_score = float(normalized.get(chunk_id, 0.0))
        ctx.stages.append(
            StageRecord(
                name=self.name,
                inputs={
                    "fts_candidates": len(fts_ranked),
                    "dense_candidates": len(dense_ranked),
                },
                outputs_per_chunk={
                    str(chunk_id): round(float(fused_score), 6)
                    for chunk_id, fused_score, _ranks in ctx.fused
                },
                timings_ms=_elapsed_ms(started),
                params={"rrf_k": ctx.settings.rrf_k},
                summary={"fused_count": len(ctx.fused)},
            )
        )


@dataclass
class GraphExpand:
    store: Store
    name: str = "graph_expand"

    def run(self, ctx: RetrievalContext) -> None:
        started = time.perf_counter()
        seeds = ctx.base_ranked[: max(5, ctx.limit)]
        ctx.neighbors = expand_neighbors(
            self.store,
            seeds,
            max_new=max(ctx.limit, 5),
        )
        for chunk_id, reason in ctx.neighbors:
            candidate = ctx.ensure_candidate(chunk_id)
            candidate.graph_reason = candidate.graph_reason or reason

        seen: set[int] = set()
        combined: list[int] = []
        for chunk_id in ctx.base_ranked:
            if chunk_id not in seen:
                combined.append(chunk_id)
                seen.add(chunk_id)
        for chunk_id, _reason in ctx.neighbors:
            if chunk_id not in seen:
                combined.append(chunk_id)
                seen.add(chunk_id)
        ctx.combined_ids = combined[: ctx.candidate_limit]

        reason_counts: dict[str, int] = {}
        for _chunk_id, reason in ctx.neighbors:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        ctx.stages.append(
            StageRecord(
                name=self.name,
                inputs={"seed_chunk_ids": seeds},
                outputs_per_chunk={
                    str(chunk_id): float(rank)
                    for rank, (chunk_id, _reason) in enumerate(ctx.neighbors, start=1)
                },
                timings_ms=_elapsed_ms(started),
                params={
                    "max_new": max(ctx.limit, 5),
                    "graph_neighbor_weight": ctx.settings.graph_neighbor_weight,
                },
                summary={
                    "neighbor_count": len(ctx.neighbors),
                    "reasons": reason_counts,
                },
            )
        )


@dataclass
class Finalize:
    store: Store
    name: str = "finalize"

    def run(self, ctx: RetrievalContext) -> None:
        started = time.perf_counter()
        combined_ids = ctx.combined_ids or ctx.base_ranked[: ctx.candidate_limit]
        hydrated = hydrate_chunks(self.store, combined_ids)
        base_ranked_set = set(ctx.base_ranked)

        results: list[SearchResult] = []
        for chunk in hydrated:
            chunk_id = int(chunk["chunk_id"])
            candidate = ctx.ensure_candidate(chunk_id)
            score = candidate.normalized_score
            if candidate.graph_reason and chunk_id not in base_ranked_set:
                score *= ctx.settings.graph_neighbor_weight
            candidate.final_score = round(float(score), 6)
            results.append(
                SearchResult(
                    chunk_id=chunk_id,
                    path=chunk["path"],
                    language=chunk.get("language"),
                    symbol_path=chunk.get("symbol_path", "") or "",
                    symbol_kind=chunk.get("symbol_kind", "") or "",
                    start_line=int(chunk["start_line"]),
                    end_line=int(chunk["end_line"]),
                    breadcrumb=chunk.get("breadcrumb", "") or "",
                    text=chunk.get("text", "") or "",
                    score=candidate.final_score,
                    components=candidate.components(),
                )
            )

        results.sort(key=lambda result: result.score, reverse=True)
        for index, result in enumerate(results):
            ctx.candidates[result.chunk_id].returned = index < ctx.limit
        ctx.results = results
        ctx.stages.append(
            StageRecord(
                name=self.name,
                inputs={"candidate_chunk_ids": combined_ids},
                outputs_per_chunk={
                    str(result.chunk_id): result.score for result in results
                },
                timings_ms=_elapsed_ms(started),
                params={"limit": ctx.limit},
                summary={
                    "candidate_count": len(results),
                    "returned_count": min(len(results), ctx.limit),
                },
            )
        )


def _persist_trace(
    *,
    store: Store,
    trace_id: str,
    ctx: RetrievalContext,
    duration_ms: int,
    returned_count: int,
    sufficiency: float,
) -> None:
    with store.tx():
        store.traces.insert_trace(
            trace_id=trace_id,
            query=ctx.query,
            tool_name="search_code",
            params={"mode": ctx.mode, "limit": ctx.limit},
            duration_ms=duration_ms,
            result_count=returned_count,
            sufficiency_score=sufficiency,
            notes={
                "pipeline_version": 1,
                "modes_used": ctx.modes_used,
                "fts_candidate_hits": len(ctx.fts_rows),
                "dense_candidate_hits": len(ctx.dense_rows),
                "candidates_considered": len(ctx.results),
                "stages": [stage.as_dict() for stage in ctx.stages],
            },
        )
        items: list[tuple[int, int | None, float, dict[str, Any], bool]] = []
        for rank, result in enumerate(ctx.results, start=1):
            items.append(
                (
                    rank,
                    result.chunk_id,
                    result.score,
                    result.components,
                    rank <= returned_count,
                )
            )
        store.traces.insert_trace_items(trace_id, items)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _embedding_text(row: dict[str, Any]) -> str:
    pieces = [
        value
        for value in (row.get("breadcrumb"), row.get("symbol_path"), row.get("text"))
        if value
    ]
    return "\n".join(str(piece) for piece in pieces)
