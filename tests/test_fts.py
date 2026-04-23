from __future__ import annotations

from repopulse.retrieval.fts import build_match_string


def test_build_match_includes_variants() -> None:
    q = build_match_string("userService")
    # camelCase split => "user" and "service"
    assert '"user"*' in q
    assert '"service"*' in q


def test_build_match_snake_and_camel() -> None:
    q = build_match_string("user_service_factory")
    assert '"factory"*' in q
    assert '"service"*' in q


def test_build_match_ignores_punctuation() -> None:
    q = build_match_string("find(): ratelimit!")
    # Must still pick up ratelimit and find.
    assert '"ratelimit"*' in q
    assert '"find"*' in q


def test_build_match_empty_string() -> None:
    assert build_match_string("") == ""
    assert build_match_string(" !@#$ ") == ""


def test_fuzzy_false_keeps_verbatim_only() -> None:
    q = build_match_string("camelCase", fuzzy=False)
    assert '"camelcase"*' in q
    assert '"camel"*' not in q
    assert '"case"*' not in q
