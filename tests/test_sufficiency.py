from __future__ import annotations

from repopulse.trace.sufficiency import confidence_label, sufficiency_score


def test_confidence_label_boundaries() -> None:
    assert confidence_label(0.35) == "uncertain"
    assert confidence_label(0.349) == "probably missing context"
    assert confidence_label(0.65) == "likely sufficient"
    assert confidence_label(0.649) == "uncertain"


def test_sufficiency_score_empty_results_is_zero() -> None:
    assert sufficiency_score([], considered=10) == 0.0
