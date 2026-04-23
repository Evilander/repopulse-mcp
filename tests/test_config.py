from __future__ import annotations

from repopulse.config import load_settings


def test_load_settings_reads_repo_config(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config_dir = tmp_path / ".repopulse"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        """
[indexer]
max_file_bytes = 42
extra_excludes = ["vendor/", "generated/"]

[retrieval]
default_limit = 7
graph_neighbor_weight = 0.75
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(tmp_path)
    assert settings.indexer.max_file_bytes == 42
    assert settings.indexer.extra_excludes == ("vendor/", "generated/")
    assert settings.retrieval.default_limit == 7
    assert settings.retrieval.graph_neighbor_weight == 0.75


def test_env_overrides_repo_config(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config_dir = tmp_path / ".repopulse"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text("[indexer]\nmax_file_bytes = 42\n", encoding="utf-8")
    monkeypatch.setenv("REPOPULSE_MAX_FILE_BYTES", "123")
    monkeypatch.setenv("REPOPULSE_EXTRA_EXCLUDES", "vendor/,generated/")
    settings = load_settings(tmp_path)
    assert settings.indexer.max_file_bytes == 123
    assert settings.indexer.extra_excludes == ("vendor/", "generated/")
