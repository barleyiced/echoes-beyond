"""Load the built dataset once and expose lookups over it."""

from __future__ import annotations

import io
import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
DATASET = DATA / "dataset.json"
DB = DATA / "dataset.db"

# Every collection that `get()` can look up by (kind, id). Anything omitted here
# resolves to None and silently drops out of scoring, so new collections must be
# added — see test_dataset.test_every_collection_is_indexed.
KINDS = ("blessings", "equations", "curios", "weighted_curios", "masks",
         "mask_gifts", "options", "domains", "beacons", "events")


@lru_cache(maxsize=1)
def load() -> dict:
    if not DATASET.exists():
        raise SystemExit(
            "data/dataset.json missing. Run:\n"
            "  python -m data.fetch\n"
            "  python -m data.build"
        )
    with io.open(DATASET, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def by_id() -> dict[tuple[str, int], dict]:
    """(kind, id) -> entry, across every collection."""
    ds = load()
    out = {}
    for kind in KINDS:
        for e in ds[kind]:
            out[(e["kind"], e["id"])] = e
    return out


def get(kind: str, entry_id: int) -> dict | None:
    return by_id().get((kind, int(entry_id)))


@lru_cache(maxsize=1)
def paths_in_theme() -> list[str]:
    """The theme's Paths in the game's own order.

    `paths` is built in AvatarBaseType order, which is the order the game itself
    lists Paths in everywhere — Remembrance, Nihility, The Hunt, Destruction,
    Elation, Propagation, Erudition, Harmony. Any Path list shown to the player
    should use it: sorting by count or alphabetically makes the same eight
    entries land somewhere different every time you look, so you have to read
    the labels instead of recognising the shape.
    """
    return [p["name"] for p in load()["paths"] if p["in_theme"]]


@lru_cache(maxsize=1)
def path_order() -> dict[str, int]:
    """Path name -> its position in the game's ordering, for sort keys.

    Unknown Paths (an id that moved between patches, or the "—" bucket for a
    blessing that resolves to nothing) sort last rather than raising.
    """
    return {p: i for i, p in enumerate(paths_in_theme())}


def path_rank(path: str) -> int:
    return path_order().get(path, len(path_order()))


@lru_cache(maxsize=1)
def characters_by_name() -> dict[str, dict]:
    return {c["name"]: c for c in load()["characters"]}


def search(query: str, kind: str | None = None, limit: int = 20) -> list[dict]:
    """Full-text search over names and descriptions via the FTS5 index.

    Used by the manual picker and, indirectly, by the OCR resolver.
    """
    if not query or not query.strip():
        return []
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        # FTS5 treats ':', '*', '^', '-', '(' and quotes as query syntax, and DU
        # names are full of colons ("Enlightenment: Wavebind"). Pasting a real
        # name would otherwise return nothing, so strip the tokens down to
        # alphanumerics and quote them.
        terms = [re.sub(r"[^0-9A-Za-z]+", "", t) for t in query.split()]
        terms = [t for t in terms if t]
        if not terms:
            return []
        # Prefix-match the final token so partial typing behaves like truncation.
        quoted = [f'"{t}"' for t in terms[:-1]]
        fts = " ".join(quoted + [f'"{terms[-1]}"*'])
        sql = "SELECT kind, entry_id FROM entries WHERE entries MATCH ?"
        args: list = [fts]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        sql += " ORDER BY rank LIMIT ?"
        args.append(limit)
        rows = con.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    return [e for e in (get(k, int(i)) for k, i in rows) if e]
