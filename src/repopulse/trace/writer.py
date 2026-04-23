"""Simple trace writer used by non-search tools.

`hybrid.search()` writes its own detailed trace (per-candidate, per-stage).
For `find_symbol`, `find_references`, `read_file` we record a compact trace:
the query or path, the tool, the count returned, and a sufficiency proxy.

This exists so the product claim "every retrieval is traced" is true —
not just the big hybrid path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from repopulse.retrieval.store import Store
from repopulse.trace import ids as trace_ids


@dataclass
class SimpleTrace:
    tool_name: str
    query: str
    params: dict[str, Any]
    items: list[dict[str, Any]] = field(default_factory=list)
    sufficiency: float = 0.0
    notes: dict[str, Any] = field(default_factory=dict)


def write(store: Store, trace: SimpleTrace) -> str:
    """Persist `trace` and return its id."""
    trace_id = trace_ids.new_id()
    with store.tx():
        store.traces.insert_trace(
            trace_id=trace_id,
            query=trace.query,
            tool_name=trace.tool_name,
            params=trace.params,
            duration_ms=int(trace.notes.get("duration_ms", 0)),
            result_count=len(trace.items),
            sufficiency_score=trace.sufficiency,
            notes=trace.notes,
        )
        items = [
            (
                rank,
                int(item.get("chunk_id", 0)) or None,
                float(item.get("score", 0.0)),
                {k: v for k, v in item.items() if k not in {"chunk_id", "score"}},
                True,
            )
            for rank, item in enumerate(trace.items, start=1)
        ]
        store.traces.insert_trace_items(trace_id, items)
    return trace_id


def timer() -> tuple[float, Any]:
    start = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - start) * 1000)

    return start, elapsed_ms
