"""Heuristic sufficiency score.

0.0 = retrieval probably missed the point; 1.0 = retrieval is clearly on-topic.

v1 is a lightweight heuristic. A learned MiniLM classifier is the obvious
post-v1 upgrade (see docs/ARCHITECTURE.md — Wen et al. 2411.06037).
"""

from __future__ import annotations

from statistics import median


def sufficiency_score(returned_scores: list[float], considered: int) -> float:
    if not returned_scores:
        return 0.0
    top = max(returned_scores)
    med = median(returned_scores) if len(returned_scores) > 1 else top
    gap = max(0.0, top - med)
    hit_factor = min(len(returned_scores) / 5.0, 1.0)
    # Tiny bonus for diversity of candidates we saw at all.
    saw_factor = min(considered / 20.0, 1.0) * 0.05
    raw = top * 0.6 + gap * 0.2 + hit_factor * 0.15 + saw_factor
    return max(0.0, min(1.0, raw))


def confidence_label(score: float) -> str:
    if score >= 0.65:
        return "likely sufficient"
    if score >= 0.35:
        return "uncertain"
    return "probably missing context"
