"""How the run is actually going, scored out of 100 — and whether that is good.

Everything else in this tool answers "what should I take next". This answers the
question you ask between Domains: *am I doing well?* The inventory view reports
what is in the bag, but forty rows of blessing names do not tell you whether the
build is coming together or quietly falling apart.

Two numbers, because one is not enough:

**Strength** is absolute. 100 is roughly what a strong finished run looks like:
live Equations, a Path stacked deep, a full bag of curios that all do something.
On Domain 3 of 13 nobody has that, so a bare strength number would read as an
insult for two thirds of every run.

**Pace** is the one you actually want. It scores a *reference run* at the same
Domain through the identical factors and compares. That comparison is the point
of building it this way: the two sides move together, so tuning a weight cannot
make the verdict drift, and "48 against a reference 41" is a claim you can argue
with in a way that "B+" is not.

The reference is deliberately unglamorous — a competent run, not an optimal one.
It holds `PICKS_PER_DOMAIN` blessings per Domain passed, concentrates most of
them on one Path, and picks up whatever Equations that concentration switches
on. Which Path a real player commits to changes what is reachable, so the
reference averages over all eight rather than assuming a good one.

Three things this deliberately is not:

* **It is not an input to any recommendation.** Nothing in `scoring`, `economy`
  or `waypoint` reads this module. A rating that fed back into advice would
  start recommending whatever made its own number go up.
* **It is not validated.** Every constant below is a guess, in the same sense
  that `engine/weights.yaml` is a guess — see the Unvalidated section of
  NOTES.md. The *shape* is defensible; the numbers are not measured.
* **It is not a prediction of whether you will clear.** It says how the build
  compares to a plausible one at the same point, and nothing about the fight.

Per the "every verdict explains itself" invariant, the return value carries the
per-factor working and a plain-language reading of the biggest gap, so a verdict
you disagree with can be argued with rather than merely resented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from engine import dataset, equations
from engine.scoring import weights
from engine.state import PICKS_PER_DOMAIN, RunState
from engine.synergy import gate_fit

# Bands on the pace ratio (strength / reference). The middle band is wide on
# purpose: the reference is a guess, so a 10% gap either way is noise and
# reporting it as a verdict would be pretending to a precision we do not have.
BANDS = [
    (1.30, "ahead", "Ahead of pace"),
    (1.08, "good", "Comfortably on track"),
    (0.88, "ontrack", "On track"),
    (0.62, "behind", "Behind pace"),
    (0.00, "poor", "Falling behind"),
]


@dataclass
class Shape:
    """The few numbers the rating actually depends on.

    Both the real run and the reference reduce to one of these, which is what
    lets a single scoring function grade them identically.
    """
    picks: float = 0.0                  # blessings held (or expected)
    blessing_value: float = 0.0         # those blessings, weighted by rarity
    best_path: float = 0.0              # deepest single Path
    best_path_name: str = ""
    paths_used: int = 0
    equation_value: float = 0.0         # sum of RARITY_VALUE over live Equations
    equations_live: int = 0
    curio_value: float = 0.0            # positive curios + triggerable weighted
    enhanced_share: float = 0.0         # 0..1 of enhanceable blessings maxed
    miracles: float = 0.0
    negatives: int = 0                  # negative curios held
    dead_weighted: int = 0              # weighted curios this team cannot fire


@dataclass
class Factor:
    key: str
    name: str
    raw: float
    weight: float
    note: str = ""
    ref_raw: float = 0.0

    @property
    def points(self) -> float:
        return self.raw * self.weight

    @property
    def ref_points(self) -> float:
        return self.ref_raw * self.weight

    def to_dict(self) -> dict:
        # `+ 0.0` normalises -0.0, which rounding a small negative produces and
        # which renders as a bare "-0" in the UI.
        return {
            "key": self.key,
            "name": self.name,
            "raw": round(self.raw, 3),
            "weight": self.weight,
            "points": round(self.points, 1) + 0.0,
            "ref_points": round(self.ref_points, 1) + 0.0,
            "gap": round(self.points - self.ref_points, 1) + 0.0,
            "note": self.note,
        }


@dataclass
class Rating:
    strength: float
    reference: float
    factors: list[Factor] = field(default_factory=list)
    reading: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.strength / self.reference if self.reference > 0.5 else 1.0

    def to_dict(self) -> dict:
        band, label = next((b, l) for cut, b, l in BANDS if self.ratio >= cut)
        return {
            "strength": round(self.strength),
            "reference": round(self.reference),
            "ratio": round(self.ratio, 2),
            "band": band,
            "label": label,
            "factors": [f.to_dict() for f in self.factors],
            "reading": self.reading,
        }


# --------------------------------------------------------------- config

def _cfg() -> dict:
    """Rating tunables, from weights.yaml so they sit with every other guess."""
    return weights()["rating"]


def _stacked(values: list[float]) -> float:
    """Live-Equation value with steep diminishing returns on the extras.

    Summing them flat breaks "concentration beats breadth", and not subtly: two
    blessings on each of six Paths satisfies sixteen of the cheap Rare Equations
    at once, which under a flat sum outscores a committed run holding a Boundary
    — the exact dilution this tool exists to argue against. So the best Equation
    counts fully and each next one counts for half of the last, which is the
    same best-track-dominates shape `equations.progress_credit` uses to score a
    single pick. Sixteen Rares come to about two thirds of one Boundary, which
    is roughly what they are worth.
    """
    decay = _cfg()["equation_decay"]
    return sum(v * decay ** i for i, v in enumerate(sorted(values, reverse=True)))


# ------------------------------------------------------------ the real run

def _blessing_rarity_value(entry: dict) -> float:
    table = weights()["rarity_value"]["blessing"]
    return table.get(entry.get("rarity", ""), 0.4)


def shape_of(run: RunState) -> Shape:
    """Reduce a real run to the numbers the rating grades."""
    counts = run.path_counts()
    held = [dataset.get("blessing", b) for b in run.owned_blessings]
    held = [b for b in held if b]

    # An enhanced Blessing is at level 2 and is genuinely worth more, so it
    # counts once and a bit rather than twice.
    bonus = _cfg()["enhanced_bonus"]
    value = sum(_blessing_rarity_value(b) * (1 + bonus if run.is_enhanced(b["id"]) else 1)
                for b in held)

    used = {p: n for p, n in counts.items() if n}
    best_name, best = max(used.items(), key=lambda kv: kv[1]) if used else ("", 0)

    # Live means held *and* its Path requirements met — both, which is the same
    # thing the Equations tile and the "not live yet" note in this module mean.
    #
    # Requirements alone is the wrong reading and not by a little: a real run at
    # Domain 10 of 20, holding four Equations, meets the Path requirements of
    # **37 of the 80** in the theme. Thirty-seven simultaneous global buffs is
    # not a game state, so meeting a requirement plainly does not grant the
    # Equation. Scoring off the loose reading maxed this factor for every
    # mid-run build and printed "37 live" directly above a tile reading "4 live".
    live = [s for s in equations.all_status(counts)
            if s.active and s.equation["id"] in run.owned_equations]

    curio_value, negatives = 0.0, 0
    ctable = weights()["rarity_value"]["curio"]
    for cid in run.owned_curios:
        c = dataset.get("curio", cid)
        if c is None:
            continue
        if c.get("is_negative"):
            negatives += 1
            continue
        curio_value += ctable.get(c.get("rarity", ""), 0.4)

    # A weighted curio the team cannot trigger is worth nothing, so it adds
    # nothing here — and is counted as drag instead.
    #
    # Only the *equipped* ones contribute. Summing over everything held credited
    # a run for six when the game allows two on the field, which is the same
    # mistake as counting Equations whose Path requirements are merely met: a
    # number that is correct under its own definition and wrong on the screen.
    # Ones held but not equipped are neither value nor drag — they are a choice
    # you have not made yet, and `weighted_plan` is where that is answered.
    dead = 0
    for wid in run.live_weighted():
        w = dataset.get("weighted_curio", wid)
        if w is None:
            continue
        fit, _ = gate_fit(w, run)
        if fit == 0.0:
            dead += 1
        else:
            curio_value += _cfg()["weighted_value"] * fit

    enhanceable = [b for b in held if b.get("max_level", 1) > 1]
    done = [b for b in enhanceable if run.is_enhanced(b["id"])]

    return Shape(
        picks=len(held),
        blessing_value=value,
        best_path=best,
        best_path_name=best_name,
        paths_used=len(used),
        equation_value=_stacked([s.value for s in live]),
        equations_live=len(live),
        curio_value=curio_value,
        enhanced_share=len(done) / len(enhanceable) if enhanceable else 0.0,
        miracles=len(run.owned_miracles),
        negatives=negatives,
        dead_weighted=dead,
    )


# ------------------------------------------------------------ the reference

@lru_cache(maxsize=256)
def _reference_equation_value(picks: int, holding: int) -> float:
    """Live-Equation value a concentrated run of `picks` blessings would have.

    Computed against the real Equation table rather than assumed, because how
    much concentration buys you is entirely a property of what the theme offers.
    Which Path you commit to changes that, and a real player's choice is not
    ours to assume — so every Path is tried as the focus and the results
    averaged. That also stops the reference flattering or punishing whichever
    Path happens to have the friendliest requirements.

    `holding` caps how many of the Equations those counts *could* switch on the
    reference is credited with, because meeting a requirement is not the same as
    having the Equation — see the note in `shape_of`. Without the cap the
    reference is credited with dozens and no real run can ever reach it.
    """
    if picks <= 0 or holding <= 0:
        return 0.0
    cfg = _cfg()
    focus_share, second_share = cfg["reference_focus"], cfg["reference_second"]
    in_theme = dataset.paths_in_theme()

    totals = []
    for i, focus in enumerate(in_theme):
        second = in_theme[(i + 1) % len(in_theme)]
        third = in_theme[(i + 2) % len(in_theme)]
        a = round(picks * focus_share)
        b = round(picks * second_share)
        counts = {focus: a, second: b, third: max(0, picks - a - b)}
        # The best `holding` of them: a reference player picks up good Equations
        # rather than random ones. Scored through the same stacking the real run
        # gets, or the yardstick would be graded on a different curve than the
        # thing it measures and the comparison would mean nothing.
        live = sorted((s.value for s in equations.all_status(counts) if s.active),
                      reverse=True)
        totals.append(_stacked(live[:holding]))
    return sum(totals) / len(totals)


def reference_shape(run: RunState) -> Shape:
    """What a competent run looks like at this Domain.

    Everything scales off Domains *passed*, not Domains total: standing on
    Domain 1 of 20 and Domain 1 of 13 are the same position, and both should
    expect an empty bag.
    """
    cfg = _cfg()
    passed = max(0, run.domain_index - 1)
    picks = PICKS_PER_DOMAIN * passed

    a = round(picks * cfg["reference_focus"])
    holding = round(passed * cfg["reference_equations_per_domain"])
    return Shape(
        picks=picks,
        blessing_value=picks * cfg["reference_rarity"],
        best_path=a,
        best_path_name="",
        paths_used=3 if picks >= 3 else max(1, int(picks)),
        equations_live=holding,
        equation_value=_reference_equation_value(int(round(picks)), holding),
        curio_value=passed * cfg["reference_curio_per_domain"],
        enhanced_share=cfg["reference_enhanced_share"] if passed >= 2 else 0.0,
        miracles=passed * cfg["reference_miracles_per_domain"],
    )


# ---------------------------------------------------------------- scoring

def _factors(s: Shape, ref: Shape, run: RunState) -> list[Factor]:
    cfg = _cfg()
    w = cfg["factors"]
    tgt = cfg["targets"]

    # Full-run targets scale with the run's own length — 20 Domains offer more
    # of everything than 13, so a fixed target would grade a long run harshly
    # and a short one generously.
    full_picks = max(1.0, PICKS_PER_DOMAIN * (run.domain_total - 1))
    cap = lambda x: max(0.0, min(1.0, x))

    def pair(fn):
        """Same formula applied to the real shape and the reference."""
        return fn(s), fn(ref)

    eq_raw, eq_ref = pair(lambda x: cap(x.equation_value / tgt["equation_value"]))
    con_raw, con_ref = pair(lambda x: cap(x.best_path / tgt["path_depth"]))
    bl_raw, bl_ref = pair(lambda x: cap(x.blessing_value / (full_picks * tgt["blessing_value_per_pick"])))
    cu_raw, cu_ref = pair(lambda x: cap(x.curio_value / tgt["curio_value"]))
    en_raw, en_ref = pair(lambda x: cap(x.enhanced_share))
    mi_raw, mi_ref = pair(lambda x: cap(x.miracles / tgt["miracles"]))

    held_eq = len(run.owned_equations)
    if s.equations_live:
        eq_note = f"{s.equations_live} of {held_eq} held are live"
    elif held_eq:
        eq_note = f"{held_eq} held, and you do not meet their Paths yet"
    else:
        eq_note = "none held"

    con_note = (f"{int(s.best_path)} on {s.best_path_name}"
                + (f", spread over {s.paths_used} Paths" if s.paths_used > 1 else "")
                ) if s.best_path else "no blessings yet"

    drag_raw = 0.0
    drag_bits = []
    if s.negatives:
        drag_raw += s.negatives * cfg["drag"]["negative_curio"]
        drag_bits.append(f"{s.negatives} negative curio(s)")
    if s.dead_weighted:
        drag_raw += s.dead_weighted * cfg["drag"]["dead_weighted"]
        drag_bits.append(f"{s.dead_weighted} weighted your team cannot fire")
    # Scatter is only a fault once there is enough in the bag to have had a
    # choice — three blessings across three Paths is just the first three offers.
    floor = weights()["thresholds"]["commitment_floor"]
    if s.picks >= cfg["drag"]["scatter_after"] and s.best_path < floor:
        drag_raw += cfg["drag"]["no_commitment"]
        drag_bits.append(f"no Path past {floor}")
    drag_raw = -min(1.0, drag_raw)

    return [
        Factor("equations", "Equations live", eq_raw, w["equations"], eq_note, eq_ref),
        Factor("concentration", "Path concentration", con_raw, w["concentration"], con_note, con_ref),
        Factor("blessings", "Blessing power", bl_raw, w["blessings"],
               f"{int(s.picks)} held", bl_ref),
        Factor("curios", "Curios working", cu_raw, w["curios"],
               f"{s.curio_value:.1f} effective", cu_ref),
        Factor("enhance", "Workbench use", en_raw, w["enhance"],
               f"{s.enhanced_share * 100:.0f}% of upgradable maxed", en_ref),
        Factor("miracles", "Miracles", mi_raw, w["miracles"],
               f"{int(s.miracles)} held", mi_ref),
        Factor("drag", "Drag", drag_raw, w["drag"],
               ", ".join(drag_bits) or "nothing dead weight", 0.0),
    ]


def _reading(factors: list[Factor], ratio: float, run: RunState) -> list[str]:
    """Plain-language why. A number with no sentence cannot be argued with."""
    out = []
    scored = [f for f in factors if f.key != "drag"]
    worst = min(scored, key=lambda f: f.points - f.ref_points)
    best = max(scored, key=lambda f: f.points - f.ref_points)

    if worst.points - worst.ref_points < -1.5:
        out.append(f"Weakest against the reference: {worst.name.lower()} "
                   f"({worst.points:.0f} vs {worst.ref_points:.0f}), {worst.note}.")
    if best.points - best.ref_points > 1.5:
        out.append(f"Carrying the run: {best.name.lower()} "
                   f"({best.points:.0f} vs {best.ref_points:.0f}), {best.note}.")

    drag = next(f for f in factors if f.key == "drag")
    if drag.points < -0.5:
        out.append(f"Costing you {abs(drag.points):.0f} points: {drag.note}.")

    left = run.domains_left()
    if ratio < 0.88 and left:
        out.append(f"{left} Domain{'' if left == 1 else 's'} left to close the gap. "
                   f"Concentration is the fastest lever, since one Equation switching "
                   f"on moves this more than several more blessings do.")
    if not out:
        out.append("No factor sits far from the reference. This is an ordinary run "
                   "for this point.")
    return out


def rate(run: RunState) -> dict:
    """Grade the run as it stands. Report only — nothing reads this back."""
    s = shape_of(run)
    ref = reference_shape(run)
    factors = _factors(s, ref, run)

    strength = max(0.0, sum(f.points for f in factors))
    reference = max(0.0, sum(f.ref_points for f in factors))

    r = Rating(strength=strength, reference=reference, factors=factors)
    r.reading = _reading(factors, r.ratio, run)
    out = r.to_dict()
    out["domain_index"] = run.domain_index
    out["domain_total"] = run.domain_total
    # Said in the payload rather than only in the UI, so anything else that ever
    # renders this cannot present it as measured.
    out["caveat"] = ("A heuristic, unvalidated against a real clear. The "
                     "reference run is a model, not a measurement.")
    return out
