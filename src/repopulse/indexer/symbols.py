"""Symbol + reference extraction via tree-sitter queries.

We keep the queries small and permissive. The goal is not a full IDE index —
it's to expose enough for `find_symbol` and for 1-hop graph expansion.
"""

from __future__ import annotations

from dataclasses import dataclass

from repopulse.languages import parse
from repopulse.tree_types import NodeLike, TreeLike


@dataclass(frozen=True)
class Symbol:
    name: str
    qualified_name: str
    kind: str  # function, class, method, constant, interface, type, module
    line: int
    col: int
    signature: str


@dataclass(frozen=True)
class Reference:
    ref_kind: str  # import, call, inherit
    target_name: str
    src_line: int


# Small, permissive symbol grammars, keyed by language. Each returns
# (kind, name_node_type, container_node_type_or_None).
_SYMBOL_RULES: dict[str, list[tuple[str, str, str | None]]] = {
    "python": [
        ("function", "function_definition", None),
        ("class", "class_definition", None),
    ],
    "javascript": [
        ("function", "function_declaration", None),
        ("class", "class_declaration", None),
        ("method", "method_definition", None),
    ],
    "typescript": [
        ("function", "function_declaration", None),
        ("class", "class_declaration", None),
        ("method", "method_definition", None),
        ("interface", "interface_declaration", None),
        ("type", "type_alias_declaration", None),
    ],
    "tsx": [
        ("function", "function_declaration", None),
        ("class", "class_declaration", None),
        ("method", "method_definition", None),
        ("interface", "interface_declaration", None),
        ("type", "type_alias_declaration", None),
    ],
    "go": [
        ("function", "function_declaration", None),
        ("method", "method_declaration", None),
        ("type", "type_declaration", None),
    ],
    "rust": [
        ("function", "function_item", None),
        ("struct", "struct_item", None),
        ("enum", "enum_item", None),
        ("trait", "trait_item", None),
        ("impl", "impl_item", None),
        ("module", "mod_item", None),
    ],
    "java": [
        ("class", "class_declaration", None),
        ("interface", "interface_declaration", None),
        ("method", "method_declaration", None),
        ("constructor", "constructor_declaration", None),
    ],
    "ruby": [
        ("class", "class", None),
        ("module", "module", None),
        ("method", "method", None),
        ("method", "singleton_method", None),
    ],
    "c": [
        ("function", "function_definition", None),
    ],
    "cpp": [
        ("function", "function_definition", None),
        ("class", "class_specifier", None),
        ("namespace", "namespace_definition", None),
    ],
}


def _child_of_type(node: NodeLike, type_name: str) -> NodeLike | None:
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _name_for(node: NodeLike, language: str) -> str:
    # Python: name is a direct `identifier`
    # JS/TS: `identifier` or `property_identifier`
    # Go: `identifier`
    # Rust: `identifier` / `type_identifier`
    # Java: `identifier`
    candidates = ("identifier", "property_identifier", "type_identifier", "field_identifier")
    for child in node.children:
        if child.type in candidates:
            return _text(child)
        if child.type == "name":
            return _text(child)
    if language == "ruby":
        for child in node.children:
            if child.type in {"constant", "identifier"}:
                return _text(child)
    return ""


def _text(node: NodeLike | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _signature_line(source: bytes, node: NodeLike) -> str:
    start = node.start_byte
    newline = source.find(b"\n", start, node.end_byte)
    end = newline if newline != -1 else node.end_byte
    return source[start:end].decode("utf-8", errors="replace").strip()


_MAX_SYMBOL_DEPTH = 500


def _walk_symbols(
    node: NodeLike,
    parents: tuple[str, ...],
    language: str,
    source: bytes,
    acc: list[Symbol],
    depth: int = 0,
) -> None:
    if depth > _MAX_SYMBOL_DEPTH:
        return
    rules = {
        rule[1]: rule[0]
        for rule in _SYMBOL_RULES.get(language, [])
    }
    if node.type in rules:
        name = _name_for(node, language)
        if name:
            kind = rules[node.type]
            qualified = ".".join(filter(None, (*parents, name)))
            acc.append(
                Symbol(
                    name=name,
                    qualified_name=qualified,
                    kind=kind,
                    line=node.start_point[0] + 1,
                    col=node.start_point[1],
                    signature=_signature_line(source, node),
                )
            )
            parents = (*parents, name)
    for child in node.children:
        _walk_symbols(child, parents, language, source, acc, depth + 1)


def extract_symbols(
    source: bytes,
    language: str | None,
    *,
    tree: TreeLike | None = None,
) -> list[Symbol]:
    if language is None:
        return []
    tree = tree or parse(source, language)
    if tree is None:
        return []
    if language not in _SYMBOL_RULES:
        return []
    acc: list[Symbol] = []
    _walk_symbols(tree.root_node, (), language, source, acc)
    return acc


# --- References (imports + calls) ---

_IMPORT_RULES: dict[str, list[str]] = {
    "python": ["import_statement", "import_from_statement"],
    "javascript": ["import_statement"],
    "typescript": ["import_statement"],
    "tsx": ["import_statement"],
    "go": ["import_declaration"],
    "rust": ["use_declaration"],
    "java": ["import_declaration"],
    "c": ["preproc_include"],
    "cpp": ["preproc_include"],
}


_CALL_RULES: dict[str, list[str]] = {
    "python": ["call"],
    "javascript": ["call_expression"],
    "typescript": ["call_expression"],
    "tsx": ["call_expression"],
    "go": ["call_expression"],
    "rust": ["call_expression", "macro_invocation"],
    "java": ["method_invocation"],
    "ruby": ["call"],
    "c": ["call_expression"],
    "cpp": ["call_expression"],
}


def _first_identifier_text(node: NodeLike) -> str:
    """Deep DFS for the first identifier-like token under `node`."""
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in {
            "identifier",
            "property_identifier",
            "type_identifier",
            "field_identifier",
            "scoped_identifier",
            "dotted_name",
            "string",
            "string_literal",
            "string_fragment",
            "raw_string_literal",
            "interpreted_string_literal",
        }:
            text = _text(current).strip("\"'`<>")
            if text:
                return text
        stack.extend(reversed(current.children))
    return ""


def extract_references(
    source: bytes,
    language: str | None,
    *,
    tree: TreeLike | None = None,
) -> list[Reference]:
    if language is None:
        return []
    tree = tree or parse(source, language)
    if tree is None:
        return []
    imports = set(_IMPORT_RULES.get(language, []))
    calls = set(_CALL_RULES.get(language, []))
    acc: list[Reference] = []

    def walk(node: NodeLike) -> None:
        if language == "ruby" and node.type == "call":
            target = _first_identifier_text(node)
            if target:
                ref_kind = (
                    "import" if target in {"require", "require_relative", "load", "autoload"} else "call"
                )
                acc.append(
                    Reference(ref_kind=ref_kind, target_name=target, src_line=node.start_point[0] + 1)
                )
        elif node.type in imports:
            target = _first_identifier_text(node)
            if target:
                acc.append(
                    Reference(ref_kind="import", target_name=target, src_line=node.start_point[0] + 1)
                )
        elif node.type in calls:
            target = _first_identifier_text(node)
            if target:
                acc.append(
                    Reference(ref_kind="call", target_name=target, src_line=node.start_point[0] + 1)
                )
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    # Cap references per file at something sane to keep DB size reasonable.
    return acc[:2000]
