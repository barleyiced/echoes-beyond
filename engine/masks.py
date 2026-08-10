"""Pick between the Masks the run offers you at the start.

An honest caveat drives the whole design here: **Masks are not team-synergy
items.** Checking the Arcadian Chronicles set, eight of the nine contain no
combat or team vocabulary whatsoever — they manipulate Domains, shops, beacons
and the draw phase. Only the Chariot Mask touches combat at all, via Instant
Kill. Anything claiming your team comp strongly determines the best Mask would
be making it up.

What *is* real and measurable:

* **Gift pool.** Each Mask draws Wishpower gifts from its own pool, and the
  pools differ substantially — 46 gifts for Fortune Cat against 108 for the
  "Two" Masks. A deeper, higher-rarity pool means more consistent levelling.
* **Wishpower income model.** How a Mask earns Wishpower is stated in its own
  text and is classified here by reading it, not by hardcoding per Mask. Some
  models are flat and dependable, others depend on routing or income.
* **Stated drawbacks.** The "Two" Masks raises its own levelling cost by 50%;
  that is in the text and is scored as a cost.

The team contributes a genuine but *weaker* signal, kept deliberately small:
a team with no sustain character benefits more from Masks that shorten or avoid
combat, and a team that needs specific Paths benefits from Masks that widen
blessing access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from engine import dataset
from engine.state import RunState

# How a Mask earns Wishpower, read from its own description.
INCOME_MODELS = {
    "domain_flat": (
        [r"upon entering a domain, gain", r"when entering a domain, obtain \d+ wishpower"],
        0.95, "flat Wishpower every Domain, the most dependable income"),
    "domain_level": (
        [r"domain of lv\.?\s*1/2/3/4/5"],
        0.80, "Wishpower scales with Domain level, so it pays more at higher difficulty"),
    "battle": (
        [r"every battle won", r"battle\(s\) won"],
        0.75, "Wishpower per battle won, steady since you cannot avoid combat"),
    "beacons": (
        [r"with 1/2/3/4 beacons"],
        0.70, "Wishpower from beacons, which this Mask also generates"),
    "occurrence": (
        [r"occurrence interaction"],
        0.55, "Wishpower only from Occurrences, so it depends on your routing"),
    "fragments": (
        [r"cosmic fragments obtained"],
        0.50, "Wishpower tied to fragment income, which is capped per Domain"),
    "removal": (
        [r"domain removed"],
        0.45, "Wishpower only when you remove Domains, which is narrow, but this Mask does it constantly"),
}

# Paths whose characters provide sustain. Note these Paths have no *blessings*
# in this theme, but characters on them still heal and shield.
SUSTAIN_PATHS = {"Abundance", "Preservation", "Remembrance"}

RARITY_WEIGHT = {"Core": 1.0, "Epic": 0.9, "Rare": 0.6, "Common": 0.3}

WEIGHTS = {
    "gift_pool": 34,
    "income": 26,
    "utility": 22,
    "team_fit": 10,     # deliberately small — see module docstring
    "drawback": 14,
}


@dataclass
class MaskScore:
    mask: dict
    factors: list = field(default_factory=list)
    recommended: bool = False

    @property
    def total(self) -> float:
        return sum(f["points"] for f in self.factors)

    def to_dict(self) -> dict:
        return {
            "id": self.mask["id"],
            "name": self.mask["name"],
            "tagline": self.mask["tagline"],
            "effect": self.mask["effect"],
            "wishpower": self.mask["wishpower"],
            "score": round(self.total, 1),
            "factors": self.factors,
            "recommended": self.recommended,
        }


def _factor(name: str, raw: float, weight: float, note: str) -> dict:
    return {"name": name, "raw": round(raw, 3), "weight": weight,
            "points": round(raw * weight, 2), "note": note}


def gift_pool(mask_id: int) -> tuple[list[dict], list[dict]]:
    """Gifts available to a Mask, plus the universal ones every Mask can draw.

    The join runs from gift to Mask: each gift lists the Masks it belongs to.
    Gifts with no Mask list are available to all of them.
    """
    exclusive, universal = [], []
    for g in dataset.load()["mask_gifts"]:
        ids = g.get("mask_ids") or []
        if not ids:
            universal.append(g)
        elif mask_id in ids:
            exclusive.append(g)
    return exclusive, universal


def income_model(mask: dict) -> tuple[str, float, str]:
    """Classify the Wishpower income from the Mask's own text."""
    text = (mask.get("wishpower") or "").lower()
    for key, (patterns, quality, note) in INCOME_MODELS.items():
        if any(re.search(p, text) for p in patterns):
            return key, quality, note
    return "unknown", 0.5, "Wishpower income could not be classified from its text"


def _drawbacks(mask: dict) -> tuple[float, str]:
    """Costs the Mask states about itself. Returns a 0..1 penalty magnitude."""
    text = f"{mask.get('effect', '')} {mask.get('tagline', '')}".lower()
    m = re.search(r"required wishpower for levelling up is increased by (\d+)%", text) \
        or re.search(r"wishpower for leveling up is increased by (\d+)%", text)
    if m:
        pct = int(m.group(1))
        return min(1.0, pct / 100.0), f"levelling costs {pct}% more Wishpower"
    if re.search(r"randomly removes", text):
        return 0.25, "removes Domains you did not choose, which cuts both ways"
    if re.search(r"will be randomi[sz]ed again", text):
        return 0.30, "re-randomises Domains, so you cannot plan a route far ahead"
    return 0.0, ""


def _utility(mask: dict, run: RunState) -> tuple[float, str]:
    """How much run-shaping leverage the Mask gives, from its stated effect."""
    text = f"{mask.get('tagline', '')} {mask.get('effect', '')}".lower()
    score, notes = 0.0, []

    if re.search(r"beacon", text):
        score += 0.35
        notes.append("adds and steers beacons")
    if re.search(r"increases their level by|level.{0,20}increase|lv\. 5", text):
        score += 0.25
        notes.append("raises Domain levels")
    if re.search(r"free|refunds|discount", text):
        score += 0.30
        notes.append("cuts shop costs")
    if re.search(r"escapade", text):
        score += 0.30
        notes.append("generates hidden Escapade Domains")
    if re.search(r"select favorable|do not enter the discard|fixed at 4", text):
        score += 0.30
        notes.append("gives you control over which Domains appear")
    if re.search(r"gain rewards twice|double", text):
        score += 0.35
        notes.append("doubles level-up rewards")
    if re.search(r"instant kill", text):
        score += 0.25
        notes.append("Instant Kill shortens fights")

    return min(1.0, score), ", ".join(notes) or "no strong run-shaping effect"


def _team_fit(mask: dict, run: RunState) -> tuple[float, str]:
    """The weak-but-real team signal. Explicitly labelled as weak in the note."""
    if not run.team:
        return 0.5, "no team set, so this factor does nothing"

    text = f"{mask.get('tagline', '')} {mask.get('effect', '')}".lower()
    paths = run.team_paths()
    has_sustain = bool(paths & SUSTAIN_PATHS)

    score, notes = 0.5, []
    if not has_sustain:
        if re.search(r"instant kill", text):
            score += 0.35
            notes.append("no sustain on your team, and Instant Kill cuts fights short")
        elif re.search(r"occurrence|reward", text):
            score += 0.20
            notes.append("no sustain on your team, and this leans away from combat")
        elif re.search(r"combat|elite|battle", text):
            score -= 0.15
            notes.append("leans into combat while your team has no sustain")
    else:
        if re.search(r"combat|elite|battle", text):
            score += 0.15
            notes.append("your team can sustain through the extra combat this wants")

    note = ", ".join(notes) if notes else "little to distinguish it for this team"
    return max(0.0, min(1.0, score)), note + " (weak signal, since Masks are economy items)"


def score_mask(mask: dict, run: RunState) -> MaskScore:
    out = MaskScore(mask=mask)

    # --- gift pool, measured from the data ---
    exclusive, universal = gift_pool(mask["id"])
    quality = sum(RARITY_WEIGHT.get(g["rarity"], 0.5) for g in exclusive)
    # 100 weighted points is a deep pool; the range across Masks is roughly 35-90.
    raw = min(1.0, quality / 80.0)
    out.factors.append(_factor(
        "gift pool", raw, WEIGHTS["gift_pool"],
        f"{len(exclusive)} Mask-specific gifts (+{len(universal)} shared), and "
        f"deeper pools level up more consistently",
    ))

    # --- Wishpower income ---
    model, qual, note = income_model(mask)
    if model == "domain_level" and run.difficulty >= 4:
        qual = min(1.0, qual + 0.10)
        note += f", and you are on Difficulty {run.difficulty}"
    out.factors.append(_factor("Wishpower income", qual, WEIGHTS["income"], note))

    # --- run-shaping utility ---
    u, unote = _utility(mask, run)
    out.factors.append(_factor("run control", u, WEIGHTS["utility"], unote))

    # --- team fit (small) ---
    t, tnote = _team_fit(mask, run)
    out.factors.append(_factor("team fit", t, WEIGHTS["team_fit"], tnote))

    # --- stated drawbacks ---
    d, dnote = _drawbacks(mask)
    if d:
        out.factors.append(_factor("drawback", -d, WEIGHTS["drawback"], dnote))

    return out


def rank(mask_ids: list[int], run: RunState) -> dict:
    """Rank the Masks the run offered you."""
    masks = [m for m in (dataset.get("mask", i) for i in mask_ids) if m]
    scored = sorted((score_mask(m, run) for m in masks), key=lambda s: -s.total)
    if scored:
        scored[0].recommended = True

    warnings = [
        "Masks in this theme shape the run economy: Domains, shops, beacons, the draw "
        "phase. They do not shape combat. Eight of the nine mention no combat "
        "mechanic at all, so your team comp barely tells them apart.",
    ]
    if not run.team:
        warnings.append("No team set, so even the small team-fit factor does nothing.")
    if len(scored) >= 2 and scored[0].total > 0:
        margin = (scored[0].total - scored[1].total) / abs(scored[0].total)
        if margin < 0.08:
            warnings.append(
                f"Close call: {scored[0].mask['name']} and {scored[1].mask['name']} are "
                "within 8%. Take whichever playstyle you prefer."
            )

    return {
        "results": [s.to_dict() for s in scored],
        "warnings": warnings,
    }
