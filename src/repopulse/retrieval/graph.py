"""1-hop graph expansion over imports and same-file neighbors."""

from __future__ import annotations

from typing import Any

from repopulse.retrieval.store import Store


def expand_neighbors(
    store: Store,
    seed_chunk_ids: list[int],
    *,
    max_new: int = 15,
) -> list[tuple[int, str]]:
    """Return up to `max_new` new chunk ids related to the seeds.

    Sources of expansion:
      * Same-file siblings of seed chunks (fresh context next to a good hit).
      * Files that import the same-file symbols (call/import graph edges).
    Returns list of (chunk_id, reason).
    """
    if not seed_chunk_ids:
        return []
    seed_set = set(seed_chunk_ids)
    # Map seed chunks to their file ids and primary symbol names.
    placeholders = ",".join("?" for _ in seed_chunk_ids)
    rows = store.raw_conn().execute(
        f"""
        SELECT c.id AS chunk_id, c.file_id AS file_id, c.symbol_path AS symbol_path
        FROM chunks c WHERE c.id IN ({placeholders})
        """,
        seed_chunk_ids,
    ).fetchall()
    file_ids = {row["file_id"] for row in rows}
    symbol_names = {
        row["symbol_path"].split(".")[-1]
        for row in rows
        if row["symbol_path"]
    }

    new: list[tuple[int, str]] = []
    added: set[int] = set()

    # Same-file siblings first.
    if file_ids:
        fplace = ",".join("?" for _ in file_ids)
        sib_rows = store.raw_conn().execute(
            f"""
            SELECT id FROM chunks
            WHERE file_id IN ({fplace})
            ORDER BY file_id, start_line
            LIMIT ?
            """,
            (*file_ids, max_new * 2),
        ).fetchall()
        for row in sib_rows:
            cid = int(row["id"])
            if cid in seed_set or cid in added:
                continue
            new.append((cid, "same_file"))
            added.add(cid)
            if len(new) >= max_new:
                return new[:max_new]

    # Import-graph neighbors: files referencing any of our symbol names.
    if symbol_names:
        nplace = ",".join("?" for _ in symbol_names)
        ref_rows = store.raw_conn().execute(
            f"""
            SELECT DISTINCT r.src_file_id AS file_id
            FROM refs r
            WHERE r.target_name IN ({nplace})
            LIMIT ?
            """,
            (*symbol_names, max_new),
        ).fetchall()
        ref_file_ids = [int(r["file_id"]) for r in ref_rows]
        if ref_file_ids:
            fplace = ",".join("?" for _ in ref_file_ids)
            chunk_rows = store.raw_conn().execute(
                f"""
                SELECT id FROM chunks
                WHERE file_id IN ({fplace})
                ORDER BY start_line
                LIMIT ?
                """,
                (*ref_file_ids, max_new),
            ).fetchall()
            for row in chunk_rows:
                cid = int(row["id"])
                if cid in seed_set or cid in added:
                    continue
                new.append((cid, "referenced_symbol"))
                added.add(cid)
                if len(new) >= max_new:
                    break

    return new[:max_new]


def hydrate_chunks(store: Store, chunk_ids: list[int]) -> list[dict[str, Any]]:
    if not chunk_ids:
        return []
    placeholders = ",".join("?" for _ in chunk_ids)
    rows = store.raw_conn().execute(
        f"""
        SELECT c.id AS chunk_id, c.symbol_path, c.symbol_kind, c.start_line, c.end_line,
               c.breadcrumb, c.text,
               f.path AS path, f.language AS language
        FROM chunks c JOIN files f ON f.id = c.file_id
        WHERE c.id IN ({placeholders})
        """,
        chunk_ids,
    ).fetchall()
    by_id = {int(r["chunk_id"]): dict(r) for r in rows}
    # Preserve caller-supplied ordering.
    return [by_id[cid] for cid in chunk_ids if cid in by_id]
