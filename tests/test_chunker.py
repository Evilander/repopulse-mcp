from __future__ import annotations

import pytest

from repopulse.indexer.chunker import ChunkOptions, chunk_source


def test_chunk_python_functions_preserves_boundaries() -> None:
    src = b"""\
def add(a, b):
    return a + b


def mul(a, b):
    return a * b


class Foo:
    def bar(self):
        return 1

    def baz(self):
        return 2
"""
    chunks = chunk_source(src, "python", ChunkOptions(max_bytes=2000, min_bytes=1))
    # We expect something non-empty.
    assert chunks, "chunker should emit at least one chunk"
    # Every chunk should be non-empty text.
    for c in chunks:
        assert c.text.strip()
        assert c.start_line >= 1
        assert c.end_line >= c.start_line
        assert c.byte_end > c.byte_start


def test_chunk_unknown_language_returns_empty() -> None:
    chunks = chunk_source(b"some text", None, ChunkOptions())
    assert chunks == []


def test_chunk_empty_source_returns_empty() -> None:
    chunks = chunk_source(b"", "python", ChunkOptions())
    assert chunks == []


def test_chunk_large_class_splits_into_methods() -> None:
    # Build a class that exceeds max_bytes so the splitter descends.
    method = "    def method_{i}(self):\n        x = {i}\n        return x\n"
    body = "\n".join(method.format(i=i) for i in range(30))
    src = f"class Big:\n{body}\n".encode()
    chunks = chunk_source(src, "python", ChunkOptions(max_bytes=400, min_bytes=50))
    # At least one chunk must reference a method to confirm recursion happened.
    joined = "\n".join(c.text for c in chunks)
    assert "method_0" in joined
    assert "method_29" in joined
    assert len(chunks) >= 2


def test_chunk_breadcrumbs_appear_for_nested() -> None:
    # Build a class big enough to force the chunker to descend past the class
    # node into its methods — only then should breadcrumbs populate.
    method_body = "        " + "x = 'padding' * 10\n        " * 20
    methods = "\n\n".join(
        f"    def method_{i}(self):\n{method_body}        return {i}"
        for i in range(5)
    )
    src = f"class Container:\n{methods}\n".encode()
    chunks = chunk_source(src, "python", ChunkOptions(max_bytes=400, min_bytes=60))
    breadcrumbs = [c.breadcrumb for c in chunks if c.breadcrumb]
    assert breadcrumbs, "expected at least one chunk to carry a breadcrumb"
    assert any("class Container" in b for b in breadcrumbs)


def test_chunk_fallback_preserves_raw_byte_offsets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = b"print('a')\n# \xff\xff\nprint('b')\n"
    monkeypatch.setattr("repopulse.indexer.chunker.parse", lambda source, language: None)
    chunks = chunk_source(
        src,
        "python",
        ChunkOptions(max_bytes=40, min_bytes=1, fallback_line_window=2),
    )
    assert len(chunks) == 2
    assert chunks[0].byte_start == 0
    assert chunks[0].byte_end == len(b"print('a')\n# \xff\xff\n")
    assert chunks[1].byte_start == chunks[0].byte_end
    assert chunks[1].byte_end == len(src)
