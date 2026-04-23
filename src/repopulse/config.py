"""Runtime configuration."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_EXCLUDES: tuple[str, ...] = (
    # VCS
    ".git/",
    ".hg/",
    ".svn/",
    # Python
    "__pycache__/",
    "*.pyc",
    ".venv/",
    "venv/",
    "env/",
    ".tox/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "*.egg-info/",
    # Node / JS
    "node_modules/",
    ".next/",
    ".nuxt/",
    ".svelte-kit/",
    # Build output
    "dist/",
    "build/",
    "out/",
    "target/",
    "bin/",
    "obj/",
    # RepoPulse state itself
    ".repopulse/",
    # Lockfiles (usually machine-generated, not meaningful text)
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "poetry.lock",
    "uv.lock",
    # Secrets
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.cer",
    "*.crt",
    "*.jks",
    "*.keystore",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".vault-token",
    "vault-token",
    "credentials.json",
    "service-account.json",
    "service_account.json",
    "secrets.yaml",
    "secrets.yml",
    "*.tfstate",
    "*.tfstate.backup",
    "*.tfvars",
    ".aws/",
    ".ssh/",
    "id_rsa",
    "id_rsa.pub",
    "id_ed25519",
    "id_ed25519.pub",
    "id_ecdsa",
    "id_ecdsa.pub",
    # Binary & large
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.bmp", "*.ico", "*.webp",
    "*.pdf", "*.zip", "*.tar", "*.gz", "*.7z", "*.rar",
    "*.mp3", "*.mp4", "*.mov", "*.avi", "*.wav", "*.flac",
    "*.woff", "*.woff2", "*.ttf", "*.otf", "*.eot",
    "*.so", "*.dll", "*.dylib",
    "*.exe",
)


class IndexerSettings(BaseModel):
    max_file_bytes: int = Field(
        default=1_000_000,
        description="Skip files larger than this many bytes.",
    )
    max_chunk_bytes: int = Field(default=2000)
    min_chunk_bytes: int = Field(default=200)
    extra_excludes: tuple[str, ...] = Field(default_factory=tuple)
    respect_gitignore: bool = True


class EmbeddingsSettings(BaseModel):
    enabled: bool = False
    model_name: str = "BAAI/bge-small-en-v1.5"
    batch_size: int = 32


class RetrievalSettings(BaseModel):
    default_limit: int = 10
    candidate_limit: int = 500
    rrf_k: int = 60
    graph_expand_hops: int = 1
    graph_neighbor_weight: float = 0.5


class Settings(BaseModel):
    indexer: IndexerSettings = Field(default_factory=IndexerSettings)
    embeddings: EmbeddingsSettings = Field(default_factory=EmbeddingsSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)


def load_settings(repo_root: Path | None = None) -> Settings:
    """Load settings from `.repopulse/config.toml`, then env overrides."""
    settings = Settings.model_validate(_load_config(repo_root))
    if (enabled := _env_flag("REPOPULSE_EMBEDDINGS")) is not None:
        settings.embeddings.enabled = enabled
    if model := os.environ.get("REPOPULSE_EMBEDDINGS_MODEL"):
        settings.embeddings.model_name = model
    if batch_size := _env_int("REPOPULSE_EMBEDDINGS_BATCH_SIZE"):
        settings.embeddings.batch_size = batch_size
    if max_file_bytes := _env_int("REPOPULSE_MAX_FILE_BYTES"):
        settings.indexer.max_file_bytes = max_file_bytes
    if max_chunk_bytes := _env_int("REPOPULSE_MAX_CHUNK_BYTES"):
        settings.indexer.max_chunk_bytes = max_chunk_bytes
    if min_chunk_bytes := _env_int("REPOPULSE_MIN_CHUNK_BYTES"):
        settings.indexer.min_chunk_bytes = min_chunk_bytes
    if extra_excludes := _env_csv("REPOPULSE_EXTRA_EXCLUDES"):
        settings.indexer.extra_excludes = tuple(extra_excludes)
    if (respect_gitignore := _env_flag("REPOPULSE_RESPECT_GITIGNORE")) is not None:
        settings.indexer.respect_gitignore = respect_gitignore
    if default_limit := _env_int("REPOPULSE_DEFAULT_LIMIT"):
        settings.retrieval.default_limit = default_limit
    if candidate_limit := _env_int("REPOPULSE_CANDIDATE_LIMIT"):
        settings.retrieval.candidate_limit = candidate_limit
    if rrf_k := _env_int("REPOPULSE_RRF_K"):
        settings.retrieval.rrf_k = rrf_k
    if graph_expand_hops := _env_int("REPOPULSE_GRAPH_EXPAND_HOPS"):
        settings.retrieval.graph_expand_hops = graph_expand_hops
    if graph_neighbor_weight := _env_float("REPOPULSE_GRAPH_NEIGHBOR_WEIGHT"):
        settings.retrieval.graph_neighbor_weight = graph_neighbor_weight
    return settings


def color_disabled() -> bool:
    """Honor NO_COLOR and REPOPULSE_ACCESSIBILITY."""
    if os.environ.get("NO_COLOR"):
        return True
    return os.environ.get("REPOPULSE_ACCESSIBILITY") in {"1", "true", "yes", "on"}


def _load_config(repo_root: Path | None) -> dict[str, Any]:
    if repo_root is None:
        return {}
    config_path = repo_root / ".repopulse" / "config.toml"
    if not config_path.exists():
        return {}
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read RepoPulse config at {config_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid RepoPulse config at {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"RepoPulse config at {config_path} must be a TOML table")
    return raw


def _env_flag(name: str) -> bool | None:
    value = os.environ.get(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean for {name}: {value!r}")


def _env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid integer for {name}: {value!r}") from exc


def _env_float(name: str) -> float | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid float for {name}: {value!r}") from exc


def _env_csv(name: str) -> list[str]:
    value = os.environ.get(name, "")
    return [part.strip() for part in value.split(",") if part.strip()]
