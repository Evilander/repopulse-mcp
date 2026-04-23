"""FTS5 query builder — turn free text into a safe MATCH string."""

from __future__ import annotations

import re

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Bound the work FTS5 does on any single query. A prompt-injected query with
# hundreds of unique tokens would otherwise trigger an unbounded prefix scan
# and stall the stdio server.
MAX_QUERY_CHARS = 4096
MAX_IDENT_TOKENS = 32


def tokenize_query(text: str) -> list[str]:
    """Public tokenizer: identifier-like tokens from free text, capped."""
    return _IDENT.findall((text or "")[:MAX_QUERY_CHARS])[:MAX_IDENT_TOKENS]


def _split_camel_snake(token: str) -> list[str]:
    """Split camelCase and snake_case tokens into subparts for broader recall."""
    parts: list[str] = []
    # snake_case
    for piece in token.split("_"):
        if not piece:
            continue
        # camelCase within the piece
        sub_parts = re.split(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", piece)
        for sp in sub_parts:
            if sp:
                parts.append(sp.lower())
    return parts


def build_match_string(user_query: str, *, fuzzy: bool = True) -> str:
    """Produce a safe FTS5 MATCH string from free-form input.

    Strategy:
      * Extract identifier-like tokens.
      * For each token: include verbatim and (optionally) its camel/snake parts.
      * Join with OR so partial hits surface; better to overfetch and rerank.
    """
    clipped = (user_query or "")[:MAX_QUERY_CHARS]
    tokens = _IDENT.findall(clipped)[:MAX_IDENT_TOKENS]
    if not tokens:
        return ""
    groups: list[str] = []
    for tok in tokens:
        variants: set[str] = {tok.lower()}
        if fuzzy:
            variants.update(_split_camel_snake(tok))
        # Quote each variant to avoid FTS5 syntax issues. Append * for prefix.
        parts = sorted(variants)
        quoted = [f'"{p}"*' for p in parts if p]
        if quoted:
            groups.append("(" + " OR ".join(quoted) + ")")
    if not groups:
        return ""
    return " OR ".join(groups)
