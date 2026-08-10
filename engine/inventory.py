"""Reconcile a scanned inventory against what the app thinks you own.

Things arrive without you choosing them — event rewards, Trotters, chest drops,
mask gifts — so the tracked state drifts from reality. A periodic screenshot of
your inventory fixes that.

The reconciliation is deliberately **not** silent. It returns an explicit diff of
what would be added and removed and waits for confirmation, because a scan that
quietly drops a blessing would rewrite your Path counts and every recommendation
that follows. Partial scans are normal — a scroll only shows part of the list —
so removals are reported as *unconfirmed* and are never applied unless you say
the scan covered everything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine import dataset
from engine.state import RunState

KIND_FIELD = {
    "blessing": "owned_blessings",
    "curio": "owned_curios",
    "weighted_curio": "owned_weighted",
    "equation": "owned_equations",
}


@dataclass
class Diff:
    kind: str
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    unchanged: int = 0
    ambiguous: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "added": self.added,
            "removed": self.removed,
            "unchanged": self.unchanged,
            "ambiguous": self.ambiguous,
        }


def _summary(kind: str, entry_id: int) -> dict:
    e = dataset.get(kind, entry_id)
    return {
        "id": entry_id,
        "name": e["name"] if e else f"{kind} {entry_id}",
        "path": (e or {}).get("path", ""),
        "rarity": (e or {}).get("rarity", ""),
    }


def reconcile(run: RunState, scanned: dict[str, list[int]],
              complete: bool = False) -> dict:
    """Compare a scan against tracked state.

    `scanned` maps kind -> ids that were confidently read. `complete` says the
    screenshot covered the entire inventory for those kinds; only then are
    removals proposed.
    """
    diffs = []
    for kind, field_name in KIND_FIELD.items():
        if kind not in scanned:
            continue
        have = list(getattr(run, field_name))
        found = list(dict.fromkeys(scanned[kind]))

        added = [i for i in found if i not in have]
        removed = [i for i in have if i not in found] if complete else []

        diffs.append(Diff(
            kind=kind,
            added=[_summary(kind, i) for i in added],
            removed=[_summary(kind, i) for i in removed],
            unchanged=len([i for i in have if i in found]),
        ).to_dict())

    total_added = sum(len(d["added"]) for d in diffs)
    total_removed = sum(len(d["removed"]) for d in diffs)

    notes = []
    if not complete and any(d["unchanged"] < len(getattr(run, KIND_FIELD[d["kind"]]))
                            for d in diffs):
        notes.append(
            "Partial scan. Anything you hold that the screenshot did not show stays put. "
            "Tick 'scan covered everything' only if the whole list was on screen."
        )
    if total_added:
        notes.append(f"Found {total_added} item(s) you were not tracking.")
    if total_removed:
        notes.append(f"{total_removed} tracked item(s) did not appear in the scan, so applying this drops them.")
    if not total_added and not total_removed:
        notes.append("Your inventory already matches what this is tracking.")

    return {
        "diffs": diffs,
        "added": total_added,
        "removed": total_removed,
        "complete": complete,
        "notes": notes,
        "requires_confirmation": bool(total_added or total_removed),
    }


def apply(run: RunState, diffs: list[dict], accept_removals: bool = False) -> RunState:
    """Apply a previously reviewed diff to the run state."""
    for d in diffs:
        field_name = KIND_FIELD.get(d["kind"])
        if not field_name:
            continue
        current = list(getattr(run, field_name))
        for item in d.get("added", []):
            if item["id"] not in current:
                current.append(item["id"])
        if accept_removals:
            drop = {item["id"] for item in d.get("removed", [])}
            current = [i for i in current if i not in drop]
        setattr(run, field_name, current)
    return run
