from __future__ import annotations

from pathlib import Path

from repopulse.config import Settings
from repopulse.indexer.run import index_repo, reset_index
from repopulse.paths import index_db_path


def test_index_creates_db_and_rows(tiny_repo: Path) -> None:
    stats = index_repo(tiny_repo)
    assert stats.files_indexed >= 3
    assert stats.chunks_written > 0
    assert stats.symbols_written > 0
    assert index_db_path(tiny_repo).exists()


def test_incremental_skips_unchanged(tiny_repo: Path) -> None:
    first = index_repo(tiny_repo)
    second = index_repo(tiny_repo)
    assert second.files_indexed == 0
    assert second.files_unchanged == first.files_indexed


def test_force_reindex_reindexes_everything(tiny_repo: Path) -> None:
    index_repo(tiny_repo)
    forced = index_repo(tiny_repo, force=True)
    assert forced.files_indexed >= 3
    assert forced.files_unchanged == 0


def test_deleted_file_is_purged_from_index(tiny_repo: Path) -> None:
    index_repo(tiny_repo)
    target = tiny_repo / "src" / "app.py"
    target.unlink()
    stats = index_repo(tiny_repo)
    assert stats.files_removed == 1


def test_reset_index_removes_db(tiny_repo: Path) -> None:
    index_repo(tiny_repo)
    assert index_db_path(tiny_repo).exists()
    reset_index(tiny_repo)
    assert not index_db_path(tiny_repo).exists()


def test_settings_default_max_chunk(tiny_repo: Path) -> None:
    settings = Settings()
    stats = index_repo(tiny_repo, settings=settings)
    assert stats.files_indexed >= 1


class _FailIfEagerDenseBackend:
    available = True

    def delete(self, chunk_ids: list[int]) -> None:
        _ = chunk_ids

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError(f"indexer should not eagerly embed {len(texts)} chunks")

    def upsert(self, items) -> None:  # type: ignore[no-untyped-def]
        raise AssertionError(f"indexer should not eagerly upsert {len(items)} vectors")


def test_index_does_not_eagerly_embed_chunks(
    tiny_repo: Path, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    backend = _FailIfEagerDenseBackend()
    monkeypatch.setattr(
        "repopulse.indexer.run._maybe_dense_backend",
        lambda store, settings: backend,
    )
    stats = index_repo(tiny_repo, settings=Settings())
    assert stats.files_indexed >= 3
    assert stats.embeddings_written == 0
