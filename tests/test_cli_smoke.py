from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from repopulse.cli.app import app

runner = CliRunner()


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "repopulse" in result.stdout.lower()


def test_cli_index_then_search_then_trace(tiny_repo: Path) -> None:
    result = runner.invoke(app, ["index", "--repo", str(tiny_repo)])
    assert result.exit_code == 0, result.stdout

    result = runner.invoke(
        app, ["search", "throttle", "--repo", str(tiny_repo), "--format", "json"]
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["trace_id"].startswith("rp_")
    assert payload["results"]

    trace_id = payload["trace_id"]
    result = runner.invoke(
        app, ["trace", trace_id, "--repo", str(tiny_repo), "--format", "json"]
    )
    assert result.exit_code == 0
    trace_payload = json.loads(result.stdout)
    assert trace_payload["trace"]["id"] == trace_id
    assert trace_payload["stages"]


def test_cli_doctor_reports_summary(tiny_repo: Path) -> None:
    runner.invoke(app, ["index", "--repo", str(tiny_repo)])
    result = runner.invoke(app, ["doctor", "--repo", str(tiny_repo)])
    assert result.exit_code == 0
    info = json.loads(result.stdout)
    assert info["files"] >= 3
    assert info["chunks"] > 0


def test_cli_doctor_without_index(
    tmp_path: Path, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    repo_root = tmp_path / "standalone"
    repo_root.mkdir()
    monkeypatch.setattr("repopulse.cli.app.find_repo_root", lambda repo=None: repo_root)
    result = runner.invoke(app, ["doctor", "--repo", str(repo_root)])
    assert result.exit_code == 2
    assert "No index" in result.stderr or "No index" in result.stdout


def test_cli_search_plain_format(tiny_repo: Path) -> None:
    runner.invoke(app, ["index", "--repo", str(tiny_repo)])
    result = runner.invoke(
        app, ["search", "token bucket", "--repo", str(tiny_repo), "--format", "plain"]
    )
    assert result.exit_code == 0
    # plain format has "replay: repopulse trace rp_..." footer.
    assert "replay: repopulse trace rp_" in result.stdout
