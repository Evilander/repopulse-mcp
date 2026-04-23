"""Reciprocal Rank Fusion.

Cormack et al. 2009 — given multiple ranked lists, combine by
  score(d) = sum_i 1 / (k + rank_i(d))
with k typically 60. Robust, parameter-light, works when score scales differ.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def fuse(
    ranked_lists: Sequence[Sequence[int]], k: int = 60
) -> list[tuple[int, float, dict[int, int]]]:
    """Fuse ranked lists of chunk ids.

    Each input is a list of chunk_ids in descending relevance order.
    Returns (chunk_id, fused_score, per_list_ranks) sorted by fused_score desc.
    """
    scores: dict[int, float] = {}
    per_list_ranks: dict[int, dict[int, int]] = {}
    for list_idx, ranked in enumerate(ranked_lists):
        for rank, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
            per_list_ranks.setdefault(chunk_id, {})[list_idx] = rank
    fused: list[tuple[int, float, dict[int, int]]] = [
        (chunk_id, score, per_list_ranks[chunk_id]) for chunk_id, score in scores.items()
    ]
    fused.sort(key=lambda row: row[1], reverse=True)
    return fused


def normalized_scores(
    fused: Iterable[tuple[int, float, dict[int, int]]],
) -> dict[int, float]:
    """Scale the fused score into [0, 1] by the top result for display."""
    items = list(fused)
    if not items:
        return {}
    top = items[0][1] or 1.0
    return {chunk_id: score / top for chunk_id, score, _ in items}
