from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from repopulse.indexer.symbols import extract_references, extract_symbols


def test_extract_python_symbols() -> None:
    src = b"""\
class Foo:
    def bar(self):
        pass

    def baz(self):
        pass


def free_function():
    pass
"""
    symbols = extract_symbols(src, "python")
    names = {s.name for s in symbols}
    assert {"Foo", "bar", "baz", "free_function"} <= names
    kinds = {s.kind for s in symbols}
    assert kinds >= {"class", "function"}


def test_extract_python_imports() -> None:
    src = b"""\
import os
from pathlib import Path
from . import config

os.getcwd()
"""
    refs = extract_references(src, "python")
    kinds = {r.ref_kind for r in refs}
    assert "import" in kinds
    assert "call" in kinds


def test_extract_typescript_symbols() -> None:
    src = b"""\
export interface User { id: string; }

export class UserService {
  addUser(user: User): void {}
  findByName(name: string): User | undefined { return undefined; }
}
"""
    symbols = extract_symbols(src, "typescript")
    names = {s.name for s in symbols}
    assert "UserService" in names
    assert "User" in names  # interface


@dataclass
class _Node:
    type: str
    text: bytes | None = None
    start_point: tuple[int, int] = (0, 0)
    children: list[_Node] = field(default_factory=list)


@dataclass
class _Tree:
    root_node: _Node


def test_extract_ruby_requires_and_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    tree = _Tree(
        root_node=_Node(
            "program",
            children=[
                _Node("call", start_point=(0, 0), children=[_Node("identifier", b"require")]),
                _Node("call", start_point=(1, 0), children=[_Node("identifier", b"puts")]),
            ],
        )
    )
    monkeypatch.setattr("repopulse.indexer.symbols.parse", lambda source, language: tree)
    refs = extract_references(b"", "ruby")
    pairs = {(ref.ref_kind, ref.target_name) for ref in refs}
    assert ("import", "require") in pairs
    assert ("call", "puts") in pairs


def test_extract_on_unknown_language_returns_empty() -> None:
    assert extract_symbols(b"print('x')", None) == []
    assert extract_references(b"print('x')", None) == []
