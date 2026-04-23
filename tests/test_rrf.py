from __future__ import annotations

from repopulse.retrieval.rrf import fuse, normalized_scores


def test_fuse_prefers_items_ranked_highly_in_multiple_lists() -> None:
    a = [1, 2, 3, 4]
    b = [2, 5, 3, 6]
    fused = fuse([a, b], k=60)
    fused_ids = [cid for cid, _, _ in fused]
    # id 2 appears near top of both -> should win.
    assert fused_ids[0] == 2


def test_fuse_empty_inputs() -> None:
    assert fuse([], k=60) == []
    assert fuse([[], []], k=60) == []


def test_fuse_single_list() -> None:
    fused = fuse([[7, 8, 9]], k=60)
    assert [cid for cid, _, _ in fused] == [7, 8, 9]


def test_normalized_scores_rescales_to_unit() -> None:
    fused = fuse([[1, 2, 3]], k=60)
    norm = normalized_scores(fused)
    assert norm[1] == 1.0
    assert 0.0 < norm[3] < norm[2] < 1.0
