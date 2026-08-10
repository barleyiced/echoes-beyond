"""Turn possibly-truncated, possibly-misread text into candidate entries.

The in-game UI clips long names, so OCR sees "Contrib..." rather than the whole
string, and the same happens when you type a few characters into the search box.
Both go through here.

Truncation is the hard part. Within Arcadian Chronicles all 144 blessing names
are unique, but they share long prefixes — 'Enlightenment:' alone covers seven
of them, and 'Acuity:' eight. Measured against the real name list:

    8 chars  -> 16/144 ambiguous (11%)
    14 chars -> 10/144 ambiguous (7%)
    20 chars ->  0/144 ambiguous (0%)

Filtering by the Path icon barely helps, because those prefix families sit
inside a single Path. What does disambiguate is the description, which is long
and near-unique and sits on the same card. So description text is the primary
signal here and the name prefix is secondary.

When the evidence does not separate the top candidates, this returns them all
and says so. A wrong auto-resolve silently corrupts the run state; a one-click
confirmation costs a second.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from engine import dataset

# Trailing ellipsis in any of the forms the UI and OCR produce.
TRUNC_RE = re.compile(r"\s*(\.{2,}|…|~)\s*$")

# Single characters OCR commonly confuses, folded before comparison so that
# "C0ntrib" and "Contrib" compare equal. Multi-character confusions such as
# rn/m are handled by the fuzzy matcher rather than here.
OCR_FOLD = str.maketrans({
    "0": "o", "1": "l", "|": "l", "5": "s", "8": "b",
})


@dataclass
class Candidate:
    entry: dict
    score: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "id": self.entry["id"],
            "kind": self.entry["kind"],
            "name": self.entry.get("name", ""),
            "path": self.entry.get("path", ""),
            "rarity": self.entry.get("rarity", ""),
            "desc": self.entry.get("desc", "") or self.entry.get("effect", ""),
            "score": round(self.score, 1),
            "reason": self.reason,
        }


@dataclass
class Resolution:
    candidates: list[Candidate]
    ambiguous: bool
    query: str
    note: str = ""

    @property
    def best(self) -> dict | None:
        return self.candidates[0].entry if self.candidates else None

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "ambiguous": self.ambiguous,
            "note": self.note,
            "candidates": [c.to_dict() for c in self.candidates],
        }


def _norm(s: str) -> str:
    s = TRUNC_RE.sub("", s or "").strip().lower()
    s = s.translate(OCR_FOLD)
    return re.sub(r"[^a-z0-9 ]+", "", s)


def was_truncated(s: str) -> bool:
    return bool(TRUNC_RE.search(s or ""))


def _pool(kind: str | None) -> list[dict]:
    ds = dataset.load()
    kinds = {
        "blessing": "blessings", "equation": "equations", "curio": "curios",
        "weighted_curio": "weighted_curios", "mask": "masks",
    }
    if kind and kind in kinds:
        return ds[kinds[kind]]
    return [e for k in kinds.values() for e in ds[k]]


def resolve(
    text: str,
    *,
    kind: str | None = None,
    desc_text: str = "",
    path: str | None = None,
    rarity: str | None = None,
    limit: int = 5,
    accept_threshold: float = 88.0,
    margin: float = 6.0,
) -> Resolution:
    """Rank entries against observed text.

    `text` is the (possibly clipped) name. `desc_text` is whatever description
    was captured from the same card — the strongest signal available. `path` and
    `rarity` are cheap constraints from the card's icon and colour.
    """
    query = (text or "").strip()
    if not query and not desc_text:
        return Resolution([], False, query, "nothing to resolve")

    pool = _pool(kind)
    if path:
        pool = [e for e in pool if not e.get("path") or e.get("path") == path] or pool
    if rarity:
        pool = [e for e in pool if not e.get("rarity") or e.get("rarity") == rarity] or pool

    nq = _norm(query)
    truncated = was_truncated(query)

    scored: list[Candidate] = []
    for e in pool:
        name = _norm(e.get("name", ""))
        if not name:
            continue

        # Name evidence. When the observed text is a prefix, a full-string ratio
        # would unfairly punish the missing tail, so score prefix containment
        # instead and only fall back to whole-name similarity otherwise.
        if nq and name.startswith(nq):
            name_score = 100.0
            why = "name prefix"
        elif nq and truncated:
            name_score = fuzz.partial_ratio(nq, name)
            why = "fuzzy prefix"
        elif nq:
            name_score = fuzz.WRatio(nq, name)
            why = "fuzzy name"
        else:
            name_score, why = 0.0, ""

        # Description evidence — the disambiguator when names collide.
        desc_score = 0.0
        if desc_text:
            target = _norm(e.get("desc", "") or e.get("effect", ""))
            if target:
                desc_score = fuzz.token_set_ratio(_norm(desc_text), target)

        if desc_text and nq:
            total = 0.45 * name_score + 0.55 * desc_score
            why = f"{why} + description"
        elif desc_text:
            total, why = desc_score, "description only"
        else:
            total = name_score

        if total > 0:
            scored.append(Candidate(e, total, why))

    scored.sort(key=lambda c: -c.score)
    top = scored[:limit]
    if not top:
        return Resolution([], False, query, "no match")

    # Ambiguous when the leader is not clearly ahead, or when a truncated query
    # is a prefix of several names and nothing else separates them.
    ambiguous = False
    note = ""
    if len(top) > 1 and (top[0].score - top[1].score) < margin:
        ambiguous = True
        note = f"{sum(1 for c in top if top[0].score - c.score < margin)} candidates score within {margin:g} points"
    elif top[0].score < accept_threshold:
        ambiguous = True
        note = f"best match only scores {top[0].score:.0f}"

    if ambiguous and not desc_text and truncated:
        note += ". Capture the description text to tell them apart"

    return Resolution(top, ambiguous, query, note)


def resolve_many(observations: list[dict], **kw) -> list[Resolution]:
    """Resolve several cards at once, e.g. the three blessings on an offer screen.

    Each observation is {'text': ..., 'desc_text': ..., 'path': ..., 'rarity': ...}.
    Once a card resolves unambiguously its entry is removed from contention for
    the others, since the game never offers the same blessing twice in one choice.
    """
    results: list[Resolution] = []
    claimed: set[tuple[str, int]] = set()
    order = sorted(range(len(observations)), key=lambda i: -len(observations[i].get("text", "")))

    slots: list[Resolution | None] = [None] * len(observations)
    for i in order:
        obs = observations[i]
        res = resolve(
            obs.get("text", ""),
            desc_text=obs.get("desc_text", ""),
            path=obs.get("path"),
            rarity=obs.get("rarity"),
            **kw,
        )
        res.candidates = [c for c in res.candidates
                          if (c.entry["kind"], c.entry["id"]) not in claimed] or res.candidates
        if not res.ambiguous and res.candidates:
            claimed.add((res.candidates[0].entry["kind"], res.candidates[0].entry["id"]))
        slots[i] = res
    return [r for r in slots if r is not None]
