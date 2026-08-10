"""Score the options the game is offering, and show the working.

Every candidate carries a per-factor breakdown. The UI renders that breakdown
rather than a bare number, because a recommendation you cannot interrogate is
one you cannot safely overrule — and overruling it is sometimes right. The
engine has no damage model and does not pretend to; this is transparent
heuristic and expected-value reasoning over the real datamined numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from engine import dataset, equations
from engine.state import RunState
from engine.synergy import element_fit, gate_fit, synergy_score, team_tags

WEIGHTS_PATH = Path(__file__).parent / "weights.yaml"


@lru_cache(maxsize=1)
def weights() -> dict:
    with open(WEIGHTS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class Factor:
    name: str
    raw: float          # 0..1 before weighting (may be negative)
    weight: float
    note: str = ""

    @property
    def points(self) -> float:
        return self.raw * self.weight

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "raw": round(self.raw, 3),
            "weight": self.weight,
            "points": round(self.points, 2),
            "note": self.note,
        }


@dataclass
class Scored:
    entry: dict
    factors: list[Factor] = field(default_factory=list)
    drivers: list[dict] = field(default_factory=list)
    blocked: str = ""           # non-empty means the option is near-worthless, with reason

    @property
    def total(self) -> float:
        return sum(f.points for f in self.factors)

    def to_dict(self) -> dict:
        return {
            "id": self.entry["id"],
            "kind": self.entry["kind"],
            "name": self.entry.get("name", ""),
            "path": self.entry.get("path", ""),
            "rarity": self.entry.get("rarity", ""),
            "desc": self.entry.get("desc", "") or self.entry.get("effect", ""),
            "score": round(self.total, 1),
            "factors": [f.to_dict() for f in self.factors],
            "equation_drivers": self.drivers,
            "blocked": self.blocked,
        }


# --------------------------------------------------------------------------

def _rarity_value(entry: dict, cfg: dict) -> float:
    kind = "curio" if entry["kind"] in ("curio", "weighted_curio") else "blessing"
    table = cfg["rarity_value"][kind]
    return table.get(entry.get("rarity", ""), 0.4)


def _dilution(entry: dict, run: RunState, cfg: dict, on_track: float) -> Factor:
    """Penalise spreading into an uncommitted Path late in the run.

    `on_track` (0..1) is how strongly this pick advances a reachable Equation.
    Branching into a new Path is only a mistake when it is aimless — a pick that
    is one step from completing an Equation is not dilution even though the Path
    is new, so the penalty is scaled down by how on-track the pick is.
    """
    w = cfg["factors"]["path_dilution"]
    path = entry.get("path")
    if not path:
        return Factor("path focus", 0.0, w, "not Path-bound")

    counts = run.path_counts()
    have = counts.get(path, 0)
    floor = cfg["thresholds"]["commitment_floor"]
    if have >= floor:
        return Factor("path focus", 0.0, w, f"already committed ({have} in {path})")

    # How bad the spread is scales with how little run is left to recover in.
    severity = (floor - have) / floor
    raw = -severity * run.progress() * (1.0 - min(1.0, on_track))
    if raw == 0:
        if on_track > 0.5:
            return Factor("path focus", 0.0, w, "new Path, but this pick is on-track")
        return Factor("path focus", 0.0, w, "early enough to branch freely")
    return Factor(
        "path focus", raw, w,
        f"only {have} in {path}, and {int(run.progress() * 100)}% through the run",
    )


def _plane_urgency(entry: dict, cfg: dict, run: RunState) -> Factor:
    """Things that compound are worth more the more Domains are left to compound in."""
    w = cfg["factors"]["plane_urgency"]
    scaling = set(cfg["scaling_tags"])
    tags = set(entry.get("tags", []))
    is_scaling = bool(tags & scaling)
    p = run.progress()
    left = run.domains_left()
    if is_scaling:
        raw = 1.0 - p                      # valuable early, dead weight late
        note = (f"compounds over the {left} Domain(s) left" if p < 0.5
                else f"only {left} Domain(s) left for this to pay off")
    else:
        raw = 0.35 + 0.65 * p              # immediate power matters more later
        note = ("immediate power" if p >= 0.5
                else f"immediate power, but {left} Domain(s) still to go")
    return Factor("timing", raw, w, note)


def _mask_synergy(entry: dict, run: RunState, cfg: dict) -> Factor:
    w = cfg["factors"]["mask_synergy"]
    mask = run.mask()
    if not mask:
        return Factor("mask", 0.0, w, "no mask selected")
    shared = set(entry.get("tags", [])) & set(mask.get("tags", []))
    if not shared:
        return Factor("mask", 0.0, w, f"no overlap with {mask['name']}")
    raw = min(1.0, 0.4 + 0.3 * len(shared))
    return Factor("mask", raw, w, f"{mask['name']}: {', '.join(sorted(shared))}")


def score_entry(entry: dict, run: RunState) -> Scored:
    cfg = weights()
    F = cfg["factors"]
    out = Scored(entry=entry)
    profile = team_tags(run)

    # --- equation progress (blessings only) ------------------------------
    if entry["kind"] == "blessing":
        credit, drivers = equations.progress_credit(
            entry["path"], run.path_counts(), run.picks_remaining(),
            owned=run.owned_equations, progress=run.progress(),
        )
        raw = min(1.0, credit / 1.5)
        completing = [d for d in drivers if d["completes"]]
        # "completes X" is only true of an Equation the run holds. Said of one it
        # does not, it is the tool being confidently wrong about the single
        # heaviest factor on the card — so the two cases get different words.
        held_done = [d for d in completing if d["held"]]
        would_done = [d for d in completing if not d["held"]]
        if held_done:
            note = "completes " + ", ".join(d["name"] for d in held_done[:2])
        elif would_done:
            note = ("would complete " + ", ".join(d["name"] for d in would_done[:2])
                    + ", which you do not hold, so this is only worth the chance of getting it")
        elif drivers:
            d = drivers[0]
            toward = d["name"] if d["held"] else f"{d['name']} (not held)"
            note = f"{d['distance']} more toward {toward}"
        else:
            note = "advances no reachable equation"
        out.factors.append(Factor("equation progress", raw, F["equation_progress"], note))
        out.drivers = drivers
        out.factors.append(_dilution(entry, run, cfg, on_track=raw))

    # --- team synergy -----------------------------------------------------
    syn, matched = synergy_score(entry.get("tags", []), profile, cfg["generic_good_tags"])
    syn *= element_fit(entry.get("elements", []), run)
    note = ", ".join(matched) if matched else "nothing the team specifically wants"
    out.factors.append(Factor("team synergy", syn, F["team_synergy"], note))

    # --- gates ------------------------------------------------------------
    if entry["kind"] == "weighted_curio":
        fit, why = gate_fit(entry, run)
        out.factors.append(Factor("team gate", fit, F["gate_fit"], why))
        if fit == 0.0:
            out.blocked = f"Your team cannot trigger this: {why}."

    # --- rarity -----------------------------------------------------------
    rv = _rarity_value(entry, cfg)
    rnote = entry.get("rarity", "")
    if entry.get("is_negative"):
        rnote += ", and this is a downside curio"
    out.factors.append(Factor("base power", rv, F["rarity"], rnote))

    # --- upgrade headroom -------------------------------------------------
    gain = entry.get("upgrade_gain")
    if gain is not None:
        raw = min(1.0, gain)
        out.factors.append(Factor(
            "upgrade headroom", raw, F["upgrade_headroom"],
            f"+{int(gain * 100)}% at the Workbench of Creation",
        ))

    out.factors.append(_mask_synergy(entry, run, cfg))
    out.factors.append(_plane_urgency(entry, cfg, run))
    return out


# --------------------------------------------------------------------------

def rank(entries: list[dict], run: RunState) -> dict:
    """Score and rank the offered options, flagging close calls and low confidence."""
    cfg = weights()
    scored = sorted((score_entry(e, run) for e in entries), key=lambda s: -s.total)
    results = [s.to_dict() for s in scored]

    close_call = False
    if len(scored) >= 2 and scored[0].total > 0:
        margin = (scored[0].total - scored[1].total) / abs(scored[0].total)
        close_call = margin < cfg["thresholds"]["close_call_margin"]

    warnings = []
    if close_call:
        warnings.append(
            f"Close call: {results[0]['name']} and {results[1]['name']} score within "
            f"{cfg['thresholds']['close_call_margin'] * 100:.0f}%. Either is defensible."
        )
    if not run.team:
        warnings.append("No team set, so synergy scoring does nothing and these ranks are generic.")
    elif scored:
        top_tags = len(scored[0].entry.get("tags", []))
        if top_tags < cfg["thresholds"]["min_tags_for_confidence"]:
            warnings.append(
                f"Low confidence: '{results[0]['name']}' has little machine-readable "
                "effect text, so its synergy score is weak evidence. Read the description."
            )

    # Every decision is made in the context of how much run is left, so that
    # context travels with every ranking rather than only with spending advice.
    from engine.economy import fragment_scarcity, spend_everything_advice

    advice = spend_everything_advice(run)
    if advice:
        warnings.append(advice)

    return {
        "results": results,
        "close_call": close_call,
        "warnings": warnings,
        "run": {
            "plane": run.plane,
            "domain_index": run.domain_index,
            "domain_total": run.domain_total,
            "domains_left": run.domains_left(),
            "endgame": run.endgame(),
            "picks_remaining": run.picks_remaining(),
            "fragment_scarcity": round(fragment_scarcity(run), 2),
            "path_counts": run.path_counts(),
        },
    }
