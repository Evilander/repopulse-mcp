"""Output formatting helpers.

Two output modes:
  * `plain`: stable, screen-reader-friendly, NO_COLOR safe, machine-greppable.
  * `rich`: panel/table rendering for interactive terminals.
Also: `json`, which is handled at the CLI layer directly.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Literal

from repopulse.config import color_disabled

OutputFormat = Literal["rich", "plain", "json"]


@dataclass
class FormatOptions:
    use_color: bool
    output_format: OutputFormat


def resolve_format(format_opt: str | None) -> FormatOptions:
    explicit = (format_opt or "").lower().strip()
    if explicit == "json":
        return FormatOptions(use_color=False, output_format="json")
    if explicit == "plain":
        return FormatOptions(use_color=False, output_format="plain")
    if explicit == "rich":
        return FormatOptions(use_color=not color_disabled(), output_format="rich")
    if sys.stdout.isatty() and not color_disabled():
        return FormatOptions(use_color=True, output_format="rich")
    return FormatOptions(use_color=False, output_format="plain")


def _get_console(opts: FormatOptions):  # type: ignore[no-untyped-def]
    from rich.console import Console

    return Console(
        no_color=not opts.use_color,
        force_terminal=opts.output_format == "rich" and sys.stdout.isatty(),
        soft_wrap=True,
    )


def render_search(response, opts: FormatOptions) -> None:  # type: ignore[no-untyped-def]
    if opts.output_format == "plain":
        _render_search_plain(response)
        return
    _render_search_rich(response, opts)


def _render_search_plain(response) -> None:  # type: ignore[no-untyped-def]
    out = sys.stdout
    if not response.results:
        out.write(f"No results for: {response.query}\n")
        out.write(f"Trace: {response.trace_id}\n")
        return
    for idx, result in enumerate(response.results, start=1):
        out.write(
            f"{idx}. {result.path}:{result.start_line}-{result.end_line}  [{result.score:.2f}]"
            f"  {result.symbol_kind}:{result.symbol_path or '(file)'}\n"
        )
    out.write(
        f"\nconsidered={response.candidates_considered} "
        f"duration_ms={response.duration_ms} "
        f"sufficiency={response.sufficiency:.2f} "
        f"modes={','.join(response.modes_used) or 'none'}\n"
    )
    out.write(f"replay: repopulse trace {response.trace_id}\n")


def _render_search_rich(response, opts: FormatOptions) -> None:  # type: ignore[no-untyped-def]
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = _get_console(opts)
    if not response.results:
        console.print(
            Panel(
                Text(f"No results for: {response.query}", style="yellow"),
                title=response.trace_id,
                border_style="yellow",
            )
        )
        return

    table = Table(
        show_header=True,
        header_style="bold cyan",
        expand=True,
        pad_edge=False,
        show_lines=False,
    )
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("path:lines", overflow="fold")
    table.add_column("score", justify="right", no_wrap=True)
    table.add_column("symbol", overflow="ellipsis")
    for idx, result in enumerate(response.results, start=1):
        loc = f"{result.path}:{result.start_line}-{result.end_line}"
        score_str = f"{result.score:.2f}"
        symbol = result.symbol_path or result.symbol_kind or "(file)"
        table.add_row(str(idx), loc, score_str, symbol)

    meta = Text()
    meta.append("considered ", style="dim")
    meta.append(f"{response.candidates_considered}")
    meta.append(" | duration ", style="dim")
    meta.append(f"{response.duration_ms}ms")
    meta.append(" | sufficiency ", style="dim")
    meta.append(f"{response.sufficiency:.2f}")
    meta.append(f" ({_confidence_label(response.sufficiency)})", style="dim italic")
    meta.append(" | modes ", style="dim")
    meta.append(", ".join(response.modes_used) or "none")

    replay = Text(f"replay: repopulse trace {response.trace_id}", style="dim")

    console.print(
        Panel(
            Group(table, Text(""), meta, replay),
            title=f"[bold]{response.trace_id}[/bold] - {response.query}",
            title_align="left",
            border_style="cyan",
        )
    )


def _confidence_label(score: float) -> str:
    from repopulse.trace.sufficiency import confidence_label

    return confidence_label(score)


def render_trace(record: dict[str, Any], opts: FormatOptions) -> None:
    if opts.output_format == "plain":
        _render_trace_plain(record)
        return
    _render_trace_rich(record, opts)


def _render_trace_plain(record: dict[str, Any]) -> None:
    out = sys.stdout
    trace = record["trace"]
    out.write(
        f"trace {trace['id']} at {trace['created_at']} "
        f"tool={trace['tool_name']} query={trace['query']!r} "
        f"returned={trace['result_count']} sufficiency={trace['sufficiency_score']:.2f}\n"
    )
    stage_summary = _format_stage_summary(record.get("stages", []))
    if stage_summary:
        out.write(f"stages {stage_summary}\n")
    for item in record["items"]:
        returned = "RETURNED" if item["returned"] else "candidate"
        path = item.get("path") or "?"
        start = item.get("start_line") or "?"
        end = item.get("end_line") or "?"
        out.write(
            f"  {item['rank']:>3}. [{item['score']:.3f}] {returned}  "
            f"{path}:{start}-{end}  {item.get('symbol_path') or ''}\n"
        )


def _render_trace_rich(record: dict[str, Any], opts: FormatOptions) -> None:
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = _get_console(opts)
    trace = record["trace"]
    table = Table(show_header=True, header_style="bold cyan", expand=True)
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("status", no_wrap=True)
    table.add_column("path:lines", overflow="fold")
    table.add_column("score", justify="right", no_wrap=True)
    table.add_column("components", overflow="fold")
    for item in record["items"]:
        status = "[green]RETURNED[/]" if item["returned"] else "[dim]candidate[/]"
        loc = (
            f"{item.get('path', '?')}:{item.get('start_line', '?')}-{item.get('end_line', '?')}"
        )
        comp = item.get("components")
        if not isinstance(comp, dict):
            comp = {}
        comp_text = ", ".join(f"{key}={value}" for key, value in comp.items() if value is not None)
        table.add_row(str(item["rank"]), status, loc, f"{item['score']:.3f}", comp_text)

    body: list[Any] = [table]
    stage_summary = _format_stage_summary(record.get("stages", []))
    if stage_summary:
        body.extend([Text(""), Text(f"stages {stage_summary}", style="dim")])

    console.print(
        Panel(
            Group(*body),
            title=(
                f"[bold]{trace['id']}[/bold] - {trace['tool_name']}  "
                f"query: {trace['query']}  "
                f"sufficiency: {float(trace['sufficiency_score']):.2f}"
            ),
            title_align="left",
            border_style="cyan",
        )
    )


def _format_stage_summary(stages: list[Any]) -> str:
    if not stages:
        return ""
    parts: list[str] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        name = str(stage.get("name") or "?")
        timings_ms = int(stage.get("timings_ms") or 0)
        parts.append(f"{name}({timings_ms}ms)")
    return " -> ".join(parts)
