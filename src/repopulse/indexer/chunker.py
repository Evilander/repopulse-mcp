"""cAST-style AST-guided chunking (Zhang et al. 2506.15655, simplified).

Strategy:
  * Walk the tree-sitter tree.
  * Emit a chunk when a node fits within MAX_CHUNK_BYTES.
  * Recurse into "semantic" children (class/function/block) when a node is too
    large.
  * Fall back to line-window slicing when the node has no semantic children.
  * Prepend breadcrumbs (parent signatures) so each chunk reads standalone.
  * After emission, merge adjacent chunks smaller than MIN_CHUNK_BYTES.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from repopulse.languages import parse
from repopulse.tree_types import NodeLike, TreeLike

# Tree-sitter node types treated as "semantic containers" worth splitting on,
# keyed by tree-sitter grammar name.
_SEMANTIC_CONTAINERS: dict[str, frozenset[str]] = {
    "python": frozenset(
        {
            "function_definition",
            "class_definition",
            "decorated_definition",
            "module",
            "block",
            "if_statement",
            "try_statement",
            "for_statement",
            "while_statement",
        }
    ),
    "javascript": frozenset(
        {
            "program",
            "function_declaration",
            "function_expression",
            "method_definition",
            "class_declaration",
            "arrow_function",
            "statement_block",
            "export_statement",
        }
    ),
    "typescript": frozenset(
        {
            "program",
            "function_declaration",
            "function_expression",
            "method_definition",
            "class_declaration",
            "arrow_function",
            "statement_block",
            "export_statement",
            "interface_declaration",
            "type_alias_declaration",
        }
    ),
    "tsx": frozenset(
        {
            "program",
            "function_declaration",
            "function_expression",
            "method_definition",
            "class_declaration",
            "arrow_function",
            "statement_block",
            "export_statement",
            "interface_declaration",
            "type_alias_declaration",
        }
    ),
    "go": frozenset(
        {
            "source_file",
            "function_declaration",
            "method_declaration",
            "type_declaration",
            "block",
        }
    ),
    "rust": frozenset(
        {
            "source_file",
            "function_item",
            "impl_item",
            "struct_item",
            "enum_item",
            "trait_item",
            "mod_item",
            "block",
        }
    ),
    "java": frozenset(
        {
            "program",
            "class_declaration",
            "method_declaration",
            "constructor_declaration",
            "interface_declaration",
            "block",
        }
    ),
    "ruby": frozenset(
        {
            "program",
            "class",
            "module",
            "method",
            "singleton_method",
            "begin",
            "do_block",
        }
    ),
    "c": frozenset({"translation_unit", "function_definition", "compound_statement"}),
    "cpp": frozenset(
        {
            "translation_unit",
            "function_definition",
            "class_specifier",
            "namespace_definition",
            "compound_statement",
        }
    ),
}

# Node types whose text we use as a breadcrumb header when we descend past them.
_BREADCRUMB_TYPES: dict[str, frozenset[str]] = {
    "python": frozenset({"function_definition", "class_definition", "decorated_definition"}),
    "javascript": frozenset(
        {"function_declaration", "class_declaration", "method_definition"}
    ),
    "typescript": frozenset(
        {
            "function_declaration",
            "class_declaration",
            "method_definition",
            "interface_declaration",
        }
    ),
    "tsx": frozenset(
        {
            "function_declaration",
            "class_declaration",
            "method_definition",
            "interface_declaration",
        }
    ),
    "go": frozenset({"function_declaration", "method_declaration", "type_declaration"}),
    "rust": frozenset(
        {"function_item", "impl_item", "struct_item", "enum_item", "trait_item", "mod_item"}
    ),
    "java": frozenset(
        {"class_declaration", "method_declaration", "interface_declaration"}
    ),
    "ruby": frozenset({"class", "module", "method", "singleton_method"}),
    "c": frozenset({"function_definition"}),
    "cpp": frozenset({"function_definition", "class_specifier", "namespace_definition"}),
}


@dataclass
class Chunk:
    text: str
    start_line: int  # 1-indexed, inclusive
    end_line: int  # 1-indexed, inclusive
    byte_start: int
    byte_end: int
    symbol_kind: str = "block"
    symbol_path: str = ""
    breadcrumb: str = ""

    @property
    def size(self) -> int:
        return self.byte_end - self.byte_start


@dataclass
class ChunkOptions:
    max_bytes: int = 2000
    min_bytes: int = 200
    fallback_line_window: int = 40  # only used when there's no parse tree.


@dataclass
class _Context:
    source: bytes
    language: str
    options: ChunkOptions
    chunks: list[Chunk] = field(default_factory=list)


def _first_line_text(source: bytes, node: NodeLike) -> str:
    start = node.start_byte
    newline = source.find(b"\n", start, node.end_byte)
    if newline == -1:
        newline = node.end_byte
    return source[start:newline].decode("utf-8", errors="replace").rstrip()


def _slice_text(source: bytes, start: int, end: int) -> str:
    return source[start:end].decode("utf-8", errors="replace")


def _node_text(source: bytes, node: NodeLike) -> str:
    return _slice_text(source, node.start_byte, node.end_byte)


def _symbol_info(node: NodeLike, language: str) -> tuple[str, str]:
    """Extract (symbol_kind, short_name) from a node."""
    kind = node.type
    name = ""
    for child in node.children:
        if child.type == "identifier" or child.type.endswith("_identifier"):
            name = _node_text_safe(child)
            break
        if child.type == "name":
            name = _node_text_safe(child)
            break
    _ = language
    return kind, name


def _node_text_safe(node: NodeLike) -> str:
    try:
        return node.text.decode("utf-8", errors="replace") if node.text else ""
    except Exception:
        return ""


def _semantic_children(node: NodeLike, language: str) -> list[NodeLike]:
    containers = _SEMANTIC_CONTAINERS.get(language, frozenset())
    return [child for child in node.children if child.type in containers]


_MAX_AST_DEPTH = 500


def _walk(node: NodeLike, parents: list[NodeLike], ctx: _Context, depth: int = 0) -> None:
    if depth > _MAX_AST_DEPTH:
        # Pathological deeply-nested source (deeply-nested macros, generated
        # code). Fall back to slicing rather than stack-overflow.
        _emit_sliced(node, parents, ctx)
        return
    size = node.end_byte - node.start_byte
    if size <= ctx.options.max_bytes:
        _emit(node, parents, ctx)
        return
    sem_children = _semantic_children(node, ctx.language)
    if sem_children:
        new_parents = [*parents, node]
        for child in sem_children:
            _walk(child, new_parents, ctx, depth + 1)
        return
    # Fallback: slice by lines.
    _emit_sliced(node, parents, ctx)


def _emit(node: NodeLike, parents: list[NodeLike], ctx: _Context) -> None:
    breadcrumbs = _breadcrumbs(parents, ctx)
    kind, name = _symbol_info(node, ctx.language)
    symbol_path = ".".join(
        filter(None, (_symbol_info(p, ctx.language)[1] for p in parents))
    )
    if name:
        symbol_path = f"{symbol_path}.{name}" if symbol_path else name
    text = _node_text(ctx.source, node)
    if not text.strip():
        return
    chunk = Chunk(
        text=text,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        byte_start=node.start_byte,
        byte_end=node.end_byte,
        symbol_kind=kind,
        symbol_path=symbol_path,
        breadcrumb=breadcrumbs,
    )
    ctx.chunks.append(chunk)


def _emit_sliced(node: NodeLike, parents: list[NodeLike], ctx: _Context) -> None:
    breadcrumbs = _breadcrumbs(parents, ctx)
    node_bytes = ctx.source[node.start_byte : node.end_byte]
    lines = node_bytes.splitlines(keepends=True)
    window = max(ctx.options.fallback_line_window, 20)
    byte = node.start_byte
    line_no = node.start_point[0] + 1
    i = 0
    while i < len(lines):
        group = lines[i : i + window]
        group_bytes = b"".join(group)
        group_text = group_bytes.decode("utf-8", errors="replace")
        byte_end = byte + len(group_bytes)
        end_line = line_no + len(group) - 1
        if group_text.strip():
            ctx.chunks.append(
                Chunk(
                    text=group_text,
                    start_line=line_no,
                    end_line=end_line,
                    byte_start=byte,
                    byte_end=byte_end,
                    symbol_kind="slice",
                    symbol_path="",
                    breadcrumb=breadcrumbs,
                )
            )
        byte = byte_end
        line_no = end_line + 1
        i += window


def _breadcrumbs(parents: list[NodeLike], ctx: _Context) -> str:
    crumbs: list[str] = []
    for parent in parents:
        crumb_types = _BREADCRUMB_TYPES.get(ctx.language, frozenset())
        if parent.type in crumb_types:
            line = _first_line_text(ctx.source, parent)
            if line:
                crumbs.append(line)
    return "\n".join(crumbs[-3:])  # keep it short — last 3 levels is enough.


def _merge_small(chunks: list[Chunk], min_bytes: int, max_bytes: int) -> list[Chunk]:
    """Greedy merge of adjacent tiny chunks inside the same file."""
    if not chunks:
        return chunks
    merged: list[Chunk] = [chunks[0]]
    for chunk in chunks[1:]:
        last = merged[-1]
        combined = last.size + chunk.size
        same_family = last.breadcrumb == chunk.breadcrumb and last.symbol_path == chunk.symbol_path
        if (last.size < min_bytes or chunk.size < min_bytes) and combined <= max_bytes and same_family:
            merged[-1] = Chunk(
                text=last.text + chunk.text,
                start_line=last.start_line,
                end_line=chunk.end_line,
                byte_start=last.byte_start,
                byte_end=chunk.byte_end,
                symbol_kind=last.symbol_kind if last.size >= chunk.size else chunk.symbol_kind,
                symbol_path=last.symbol_path,
                breadcrumb=last.breadcrumb,
            )
        else:
            merged.append(chunk)
    return merged


def _line_window_chunks(source: bytes, options: ChunkOptions) -> list[Chunk]:
    """Text-only chunking for unparseable files: fixed line windows."""
    lines = source.splitlines(keepends=True)
    chunks: list[Chunk] = []
    window = options.fallback_line_window
    i = 0
    byte = 0
    line_no = 1
    while i < len(lines):
        group = lines[i : i + window]
        group_bytes = b"".join(group)
        group_text = group_bytes.decode("utf-8", errors="replace")
        byte_end = byte + len(group_bytes)
        end_line = line_no + len(group) - 1
        if group_text.strip():
            chunks.append(
                Chunk(
                    text=group_text,
                    start_line=line_no,
                    end_line=end_line,
                    byte_start=byte,
                    byte_end=byte_end,
                    symbol_kind="line_window",
                )
            )
        i += window
        byte = byte_end
        line_no = end_line + 1
    return chunks


def chunk_source(
    source: bytes,
    language: str | None,
    options: ChunkOptions | None = None,
    *,
    tree: TreeLike | None = None,
) -> list[Chunk]:
    """Return a list of chunks for `source`.

    Handles three cases:
      1. Known language with parser available -> cAST split.
      2. Known language, no parser -> line-window fallback.
      3. Unknown language (caller shouldn't pass this; skip).
    """
    opts = options or ChunkOptions()
    if not source:
        return []
    if language is None:
        return []
    tree = tree or parse(source, language)
    if tree is None:
        return _line_window_chunks(source, opts)
    ctx = _Context(source=source, language=language, options=opts)
    _walk(tree.root_node, [], ctx)
    if not ctx.chunks:
        return _line_window_chunks(source, opts)
    return _merge_small(ctx.chunks, opts.min_bytes, opts.max_bytes)
