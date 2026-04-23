from __future__ import annotations

import json

import pytest

from repopulse.cli.install import (
    AgentTarget,
    _escape_toml,
    install_codex_target,
    install_json_target,
)


def test_install_json_preserves_existing_servers(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "claude.json"
    path.write_text(
        json.dumps({"mcpServers": {"other": {"command": "python", "args": ["x"]}}}),
        encoding="utf-8",
    )
    install_json_target(AgentTarget("Claude Code", path, "json"), tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "other" in payload["mcpServers"]
    assert "repopulse" in payload["mcpServers"]


def test_install_json_refuses_invalid_existing_config(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "claude.json"
    original = '{"mcpServers": '
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        install_json_target(AgentTarget("Claude Code", path, "json"), tmp_path)
    assert path.read_text(encoding="utf-8") == original


def test_install_codex_refuses_invalid_existing_config(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.toml"
    original = "[mcp_servers.repopulse\ncommand = \"python\"\n"
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid TOML"):
        install_codex_target(AgentTarget("OpenAI Codex", path, "toml-codex"), tmp_path)
    assert path.read_text(encoding="utf-8") == original


def test_escape_toml_escapes_control_characters() -> None:
    assert _escape_toml('a"b\\c\n\t') == 'a\\"b\\\\c\\n\\t'
