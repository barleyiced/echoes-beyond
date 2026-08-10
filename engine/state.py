"""The state of one Divergent Universe run."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from engine import dataset

RUNS = Path(__file__).resolve().parent.parent / "runs"
HISTORY = RUNS / "history"

# `runs/current.json` is the only state in this project that cannot be
# regenerated — the dataset rebuilds from pinned game data any time, a run is
# somebody's evening. It is also written on nearly every keystroke, since the UI
# saves after every edit. That combination is what the two mechanisms below are
# for: the write itself cannot half-happen, and what it replaced is kept.

# Snapshots retained. Small files (~1.4 KB), so this is generous on purpose.
MAX_HISTORY = 40

# Consecutive edits inside this window collapse into one snapshot. Without it,
# typing "1144" into the fragments box would burn four of them.
COALESCE_SECONDS = 25

# Lists whose shrinking means something was lost rather than merely changed.
OWNED_FIELDS = ("owned_blessings", "owned_curios", "owned_weighted",
                "equipped_weighted", "owned_equations", "owned_miracles",
                "enhanced_blessings", "team")


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename.

    A plain `write_text` truncates first, so a crash or a full disk between
    truncate and write leaves an empty or half-written run. `os.replace` is
    atomic on Windows and POSIX alike, so the file is either the old content or
    the new one and never a prefix of either.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _counts(state: dict) -> dict[str, int]:
    return {f: len(state.get(f) or []) for f in OWNED_FIELDS}


def _loses_something(old: dict, new: dict) -> bool:
    """True when the new state holds strictly less of anything than the old.

    This is the case worth protecting: a reset, a mis-click on a × button, an
    OCR scan that removed more than it should. Ordinary growth does not need to
    interrupt coalescing.
    """
    a, b = _counts(old), _counts(new)
    return any(b[k] < a[k] for k in a)


def _snapshot(text: str, forced: bool) -> None:
    """Keep `text` (the state being replaced) in runs/history.

    `forced` skips coalescing — used when the save destroys something, so the
    state just before a reset is always recoverable no matter how recently
    anything else was written.
    """
    HISTORY.mkdir(parents=True, exist_ok=True)
    existing = sorted(HISTORY.glob("*.json"))

    if existing and not forced:
        newest = existing[-1]
        if time.time() - newest.stat().st_mtime < COALESCE_SECONDS:
            # Same edit burst: move the window forward rather than adding a file.
            # The *older* content is the one worth keeping, so this leaves the
            # existing snapshot alone and simply declines to add another.
            return

    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3]
    _atomic_write(HISTORY / f"{stamp}.json", text)

    for old in sorted(HISTORY.glob("*.json"))[:-MAX_HISTORY]:
        old.unlink(missing_ok=True)


def history() -> list[dict]:
    """Recent snapshots, newest first, each described well enough to choose one."""
    out = []
    for p in sorted(HISTORY.glob("*.json"), reverse=True):
        try:
            state = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue          # a truncated snapshot is not worth a 500
        c = _counts(state)
        out.append({
            "file": p.name,
            "saved_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            "domain_index": state.get("domain_index"),
            "domain_total": state.get("domain_total"),
            "counts": c,
            "total": sum(v for k, v in c.items() if k != "team"),
        })
    return out


def restore(filename: str) -> "RunState":
    """Make a snapshot current again.

    Restoring is itself a save, so the state being replaced is snapshotted on
    the way past — going back is undoable in the same way everything else is.
    Only a bare filename from `history()` is accepted; anything with a path
    separator is rejected rather than resolved, so a crafted request cannot read
    outside runs/history.
    """
    if filename != Path(filename).name or not filename.endswith(".json"):
        raise ValueError("not a snapshot name")
    path = HISTORY / filename
    if not path.is_file():
        raise FileNotFoundError(filename)
    state = RunState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    state.save(force_snapshot=True)
    return state

# Arcadian Chronicles runs three Planes, and the run length is not fixed. From
# RoguePersonaLayerRoom cross-referenced with RogueTournArea:
#
#   4+5+4 = 13 Domains   Difficulty 1-5
#   5+7+5 = 17 Domains   Difficulty 5 only
#   6+8+6 = 20 Domains   Difficulty 5 only
#
# So 13 is the common case and 17/20 appear at the top difficulty. The exact
# total is whatever the game's own counter says, which is why it is an input
# rather than a constant — `run_lengths` in the dataset lists the known variants.
PLANES = 3
DOMAINS_TOTAL_DEFAULT = 13

# Blessing choices per Domain, used to estimate how many picks remain. A tunable
# estimate, not a fact from the data.
PICKS_PER_DOMAIN = 1.4


@dataclass
class Character:
    name: str
    path: str = ""
    element: str = ""

    @classmethod
    def from_name(cls, name: str) -> "Character":
        c = dataset.characters_by_name().get(name)
        if c:
            return cls(name=c["name"], path=c["path"], element=c["element"])
        return cls(name=name)


@dataclass
class RunState:
    mask_id: int | None = None
    wishpower_level: int = 0
    plane: int = 1
    difficulty: int = 1

    # Position within the run, as the game shows it (e.g. 15 of 17). This drives
    # end-of-run behaviour: currency you cannot spend before the run ends is
    # wasted, so the closer these two numbers get, the cheaper spending becomes.
    domain_index: int = 1
    domain_total: int = DOMAINS_TOTAL_DEFAULT

    team: list[Character] = field(default_factory=list)

    # Blessings are stored as ids; path counts are derived from them so the two
    # can never drift apart.
    owned_blessings: list[int] = field(default_factory=list)

    # Blessings already taken to max at a Workbench — the game shows them as
    # "Already Enhanced" / "Max Level Reached". Every Blessing in this theme has
    # exactly two levels, so enhancing is one-shot and this is a set rather than
    # a level counter (test_every_blessing_has_exactly_two_levels guards that).
    # They are not candidates at any future bench, which is the whole point of
    # tracking it: without this the Workbench keeps recommending work you have
    # already done.
    enhanced_blessings: list[int] = field(default_factory=list)
    owned_curios: list[int] = field(default_factory=list)

    # Weighted Curios are *equipped*, not merely held: the screen reads
    # "Weighted Curios Equipped: 0/2" with a third socket locked. So holding six
    # is not holding six — at most `weighted_slots` of them are live at once, and
    # which two is a real recurring decision. Anything valuing what the run has
    # must intersect with `equipped_weighted`, the same way Equation value has to
    # intersect with owned_equations rather than with "requirements met".
    owned_weighted: list[int] = field(default_factory=list)
    equipped_weighted: list[int] = field(default_factory=list)

    # Observed as 2 unlocked of 3 sockets. What unlocks the third is unknown, so
    # it is an editable number rather than a constant.
    weighted_slots: int = 2

    def live_weighted(self) -> list[int]:
        """The Weighted Curios actually in effect.

        Falls back to the first `weighted_slots` held when nothing has been
        marked equipped, so a run recorded before this existed is under-counted
        rather than over-counted — the failure this whole field exists to stop.
        """
        if self.equipped_weighted:
            return [w for w in self.equipped_weighted
                    if w in self.owned_weighted][:self.weighted_slots]
        return self.owned_weighted[:self.weighted_slots]

    def is_equipped(self, weighted_id: int) -> bool:
        return weighted_id in self.live_weighted()
    owned_equations: list[int] = field(default_factory=list)

    # Wishpower Miracles taken so far, and how many times you can still reshuffle
    # the hand a level-up offers. Resets are granted per run (the Time talent
    # line gives three), not per level, so they are tracked as a balance.
    owned_miracles: list[int] = field(default_factory=list)
    miracle_resets: int = 0

    # Redraw attempts left at the "Select Next Destination" screen. Granted per
    # run and topped up by Mask lines, so it is a balance rather than a constant.
    door_redraws: int = 0

    fragments: int = 0

    # Heat is the Workbench of Creation currency. It is granted per Workbench and
    # reset when you leave one, so it is tracked as "what is in front of me now"
    # rather than as a running balance.
    heat: int = 0
    heat_max: int = 0

    # Enhancing costs more for a rarer Blessing — 1, 2 or 3 Heat. This is *not*
    # in the datamined tables (RogueTournWorkbench only lists which functions a
    # bench offers, and no cost table exists upstream at the pinned commit); it
    # is reported from play, so it is editable rather than hardcoded. Because the
    # costs differ, the question at a bench is not "what is best" but "which
    # affordable *set* is best" — see economy.plan_workbench.
    heat_costs: dict[str, int] = field(
        default_factory=lambda: {"Common": 1, "Rare": 2, "Legendary": 3})

    # Superseded by heat_costs; kept so older saved runs still load, and used as
    # the fallback for a Blessing whose rarity is not in the table.
    heat_per_enhance: int = 1

    # Herta's Curio Store prices by rarity, in Cosmic Fragments. Observed from
    # play — a 1-star card reads 100 and a 2-star 180 — and *not* in the data:
    # no price table exists upstream at the pinned commit, checked the same way
    # heat_costs was. Legendary is an extrapolation and has not been seen, so it
    # is prefill only: the price actually charged is typed per card at the shelf.
    store_prices: dict[str, int] = field(
        default_factory=lambda: {"Common": 100, "Rare": 180, "Legendary": 300})

    # The Blessing Store is a *different shop* and charges differently — 80 /
    # 120 / 180 by rarity, all three read off one screen, so unlike the Curio
    # Store's Legendary this table is fully observed.
    blessing_prices: dict[str, int] = field(
        default_factory=lambda: {"Common": 80, "Rare": 120, "Legendary": 180})

    def store_price(self, entry: dict) -> int:
        table = (self.blessing_prices if entry.get("kind") == "blessing"
                 else self.store_prices)
        return int(table.get(entry.get("rarity", ""), 0))

    def enhance_cost(self, blessing: dict) -> int:
        """Heat to enhance one Blessing, by its rarity.

        Confirmed against the Enhancing Blessings screen: a 1/2/3-star card shows
        1/2/3 Heat on its cost pip.
        """
        return int(self.heat_costs.get(blessing.get("rarity", ""), self.heat_per_enhance))

    def is_enhanced(self, blessing_id: int) -> bool:
        return blessing_id in self.enhanced_blessings

    def can_enhance(self, blessing: dict) -> bool:
        """Whether this Blessing is still a Workbench candidate at all."""
        return (blessing.get("max_level", 1) > 1
                and not self.is_enhanced(blessing["id"]))

    def blessing_level(self, blessing_id: int) -> int:
        return 2 if self.is_enhanced(blessing_id) else 1

    notes: str = ""

    # ---------------------------------------------------------------- derived

    def path_counts(self) -> dict[str, int]:
        counts = {p: 0 for p in dataset.paths_in_theme()}
        for bid in self.owned_blessings:
            b = dataset.get("blessing", bid)
            if b:
                counts[b["path"]] = counts.get(b["path"], 0) + 1
        return counts

    def domains_left(self) -> int:
        """Domains still to come, including the one you are standing in."""
        return max(0, self.domain_total - self.domain_index + 1)

    def picks_remaining(self) -> int:
        """Rough number of blessing choices left in the run."""
        return max(0, int(self.domains_left() * PICKS_PER_DOMAIN))

    def progress(self) -> float:
        """0.0 at the very start of the run, 1.0 at the final Domain.

        Uses the Domain counter when it is set, since 15/17 says far more about
        how much run is left than "Plane 4" does.
        """
        if self.domain_total > 1:
            return min(1.0, max(0.0, (self.domain_index - 1) / (self.domain_total - 1)))
        return min(1.0, max(0.0, (self.plane - 1) / max(1, PLANES - 1)))

    def endgame(self) -> bool:
        """True when there is too little run left to bank anything for later."""
        return self.domains_left() <= 3

    def team_paths(self) -> set[str]:
        return {c.path for c in self.team if c.path}

    def team_elements(self) -> set[str]:
        return {c.element for c in self.team if c.element}

    def mask(self) -> dict | None:
        return dataset.get("mask", self.mask_id) if self.mask_id else None

    # ------------------------------------------------------------- persistence

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RunState":
        d = dict(d)
        d["team"] = [Character(**c) if isinstance(c, dict) else Character.from_name(c)
                     for c in d.get("team", [])]
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def save(self, name: str = "current", force_snapshot: bool = False) -> Path:
        """Persist the run, keeping whatever it replaced.

        `force_snapshot` bypasses coalescing for a save that is a deliberate
        jump rather than part of an edit burst — a restore is the case, and
        without it the state a restore replaced was silently dropped whenever
        anything else had been saved in the last few seconds, which made going
        back a second way to lose work.
        """
        RUNS.mkdir(exist_ok=True)
        path = RUNS / f"{name}.json"
        text = json.dumps(self.to_dict(), indent=1)

        # Snapshot what is being replaced, not what is being written: the point
        # is to get back to the state before a bad edit, and that state is the
        # one already on disk.
        if path.exists():
            try:
                prev = path.read_text(encoding="utf-8")
                if prev.strip() and prev != text:
                    _snapshot(prev, forced=force_snapshot or _loses_something(
                        json.loads(prev), self.to_dict()))
            except (OSError, ValueError):
                pass          # an unreadable previous file must not block the save

        _atomic_write(path, text)
        return path

    @classmethod
    def load(cls, name: str = "current") -> "RunState":
        path = RUNS / f"{name}.json"
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
