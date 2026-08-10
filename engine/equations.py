"""Which Equations are still reachable, and how far away each one is.

This is the part that stops a run being quietly ruined. Equations need N
blessings of one Path plus M of another; spreading picks across five Paths feels
fine for two Planes and then activates nothing. Every blessing is scored partly
on how much closer it moves you to an Equation worth having.

Arcadian Chronicles offers no Preservation or Abundance blessings, so Equations
requiring them are not merely far away — they are impossible. Those are reported
as unreachable rather than expensive.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine import dataset

# Relative worth of each Equation tier. Boundary Equations (PathEcho) are the
# run-defining ones and need 16 of a single Path.
RARITY_VALUE = {
    "PathEcho": 1.0,
    "Legendary": 0.85,
    "Epic": 0.6,
    "Rare": 0.35,
}

# What an Equation you do *not* hold is worth as a target.
#
# Meeting an Equation's Path requirements is not having it — the same distinction
# that caught rating.py ("37 live" over a tile reading "4 live") and Weighted
# Curios (held is not equipped). Scoring did not draw it at all: `progress_credit`
# read the whole 80-Equation catalog and credited a pick with completing things
# the run had never acquired, at full value, on the last Domain of a run.
#
# The fix is not to intersect with `owned_equations`, which would read 0 on every
# card at the start of a run and destroy the concentration factor this tool exists
# for (invariant 5). An unheld Equation carries *option* value: line your Path
# counts up with it and it pays off if you acquire it. That option decays as the
# run runs out, because "would complete it if you got it" describes an event with
# fewer and fewer chances left to happen.
#
# UNHELD_FLOOR is what survives the decay: concentration is worth something even
# if that specific Equation never arrives, since another on the same Path may.
UNHELD_FLOOR = 0.15


def unheld_discount(progress: float) -> float:
    """How much of an unheld Equation's value still counts, given run progress.

    Full value at the start of a run, `UNHELD_FLOOR` at the end. Linear because
    the acquisition rate it stands in for is not known — see NOTES.md; this is
    an `estimate`, not a measurement.
    """
    return UNHELD_FLOOR + (1.0 - UNHELD_FLOOR) * (1.0 - min(1.0, max(0.0, progress)))


@dataclass
class EquationStatus:
    equation: dict
    distance: int              # blessings still needed
    missing: dict[str, int]    # path -> how many more
    active: bool
    reachable: bool            # every required Path exists in this theme
    blocked_paths: list[str]   # required Paths the theme does not offer

    @property
    def value(self) -> float:
        return RARITY_VALUE.get(self.equation["rarity"], 0.3)

    def to_dict(self) -> dict:
        return {
            "id": self.equation["id"],
            "name": self.equation["name"],
            "rarity": self.equation["rarity"],
            # "PathEcho" is the internal name; in game these are Boundary Equations.
            "rarity_label": ("Boundary" if self.equation["is_boundary"]
                             else self.equation["rarity"]),
            "is_boundary": self.equation["is_boundary"],
            "desc": self.equation["desc"],
            "requires": self.equation["requires"],
            "distance": self.distance,
            "missing": self.missing,
            "active": self.active,
            "reachable": self.reachable,
            "blocked_paths": self.blocked_paths,
            "value": round(self.value, 3),
        }


def status_for(equation: dict, path_counts: dict[str, int],
               in_theme: set[str] | None = None) -> EquationStatus:
    in_theme = in_theme if in_theme is not None else set(dataset.paths_in_theme())
    missing: dict[str, int] = {}
    blocked: list[str] = []
    for req in equation["requires"]:
        path, need = req["path"], req["count"]
        if path not in in_theme:
            blocked.append(path)
        have = path_counts.get(path, 0)
        if have < need:
            missing[path] = need - have
    return EquationStatus(
        equation=equation,
        distance=sum(missing.values()),
        missing=missing,
        active=not missing,
        reachable=not blocked,
        blocked_paths=blocked,
    )


def all_status(path_counts: dict[str, int]) -> list[EquationStatus]:
    in_theme = set(dataset.paths_in_theme())
    return [status_for(e, path_counts, in_theme) for e in dataset.load()["equations"]]


def reachable(path_counts: dict[str, int], picks_remaining: int,
              include_active: bool = True) -> list[EquationStatus]:
    """Equations that could still be completed with the picks left, best first."""
    out = []
    for st in all_status(path_counts):
        if not st.reachable:
            continue
        if st.active and not include_active:
            continue
        if st.distance > picks_remaining:
            continue
        out.append(st)
    # Rank by value *and* proximity. Sorting on value alone puts all eight
    # Boundary Equations at the top of an empty run at 16 blessings away, which
    # is true but useless — what you want to see first is what is both good and
    # actually close.
    out.sort(key=lambda s: -(s.value / (s.distance + 1)))
    return out


def _track_value(st: EquationStatus, picks_left: int) -> float:
    """How good a target this Equation is right now.

    Combines three things:
      value      — Boundary > Legendary > Epic > Rare
      proximity  — how much of the requirement is already paid for
      budget     — whether finishing it is comfortable within the picks left

    An Equation needing 16 Nihility when you hold none is technically reachable
    with 18 picks left, but it would consume the whole run; the budget term is
    what stops the engine treating that as equivalent to finishing something you
    are already most of the way through.
    """
    if st.distance == 0:
        return st.value
    proximity = 1.0 - st.distance / max(1, st.equation["total_required"])
    budget = 1.0 - min(1.0, st.distance / max(1, picks_left))
    return st.value * (0.35 + 0.65 * proximity) * (0.40 + 0.60 * budget)


def progress_credit(path: str, path_counts: dict[str, int], picks_remaining: int,
                    owned: list[int] | set[int] | None = None,
                    progress: float = 0.0) -> tuple[float, list[dict]]:
    """How much taking one more blessing of `path` advances your Equation prospects.

    Credit is the value of the *best* track this pick advances, not the sum over
    all of them. Summing rewarded breadth — opening five distant Equations beat
    advancing one you were committed to, which is precisely the dilution that
    ruins runs. Taking the best track, plus a small allowance for flexibility,
    makes concentration win.

    `owned` is the Equations the run actually holds; anything else is discounted
    by `unheld_discount(progress)` rather than counted in full. Every driver
    carries `held` so a caller can say "completes X" only when that is true —
    the wording is not cosmetic, since an unheld completion is a claim about a
    thing that has not happened and may never (invariant 1).

    Defaults mean "holds nothing, start of run", i.e. no discount — so a caller
    that does not pass run state gets the most generous reading, which is the
    right way round for a term that is otherwise silently deflated.

    Returns the credit and the Equations that drove it, so the UI can explain
    why a pick scored well rather than merely asserting it.
    """
    owned = set(owned or ())
    discount = unheld_discount(progress)
    bumped = dict(path_counts)
    bumped[path] = bumped.get(path, 0) + 1
    picks_after = max(0, picks_remaining - 1)

    before = {s.equation["id"]: s for s in all_status(path_counts)}
    drivers = []

    for post in all_status(bumped):
        if not post.reachable:
            continue
        pre = before.get(post.equation["id"])
        if pre is None or post.distance >= pre.distance:
            continue                      # this pick did not advance it
        if post.distance > picks_after:
            continue                      # cannot be finished in the run that remains
        gain = _track_value(post, picks_after)
        completes = post.active and not pre.active
        if completes:
            gain = post.value             # finishing is worth full value
        held = post.equation["id"] in owned
        if not held:
            gain *= discount
        drivers.append({
            "id": post.equation["id"],
            "name": post.equation["name"],
            "rarity": post.equation["rarity"],
            "distance": post.distance,
            "completes": completes,
            "held": held,
            "gain": round(gain, 3),
        })

    if not drivers:
        return 0.0, []

    drivers.sort(key=lambda d: -d["gain"])
    # Best track dominates; the runner-up contributes a little, because keeping a
    # second option open has real value when future offers are random.
    credit = drivers[0]["gain"]
    if len(drivers) > 1:
        credit += 0.15 * drivers[1]["gain"]
    return credit, drivers[:4]


def path_summary(path_counts: dict[str, int], picks_remaining: int) -> list[dict]:
    """Per-Path view for the run screen: how many held, and what it is building toward."""
    out = []
    for path in dataset.paths_in_theme():
        have = path_counts.get(path, 0)
        targets = [s for s in reachable(path_counts, picks_remaining)
                   if any(r["path"] == path for r in s.equation["requires"]) and not s.active]
        targets.sort(key=lambda s: (s.distance, -s.value))
        out.append({
            "path": path,
            "count": have,
            "next_target": targets[0].to_dict() if targets else None,
        })
    return out
