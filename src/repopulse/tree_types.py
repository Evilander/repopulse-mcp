"""Minimal structural types for tree-sitter nodes/trees."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class NodeLike(Protocol):
    type: str
    text: bytes | None
    children: Sequence[NodeLike]
    start_byte: int
    end_byte: int
    start_point: tuple[int, int]
    end_point: tuple[int, int]


class TreeLike(Protocol):
    root_node: NodeLike
