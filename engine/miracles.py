"""Which Wishpower Miracle to take when the Mask levels up.

Every Wishpower level offers a small hand of Miracles (`RoguePersonaStyleGift`
upstream — the game shows them as Ordinary / Rare / Extraordinary Miracles, plus
the Core lines that upgrade your own Mask). Three things make this a different
problem from picking a blessing:

1. **Names carry no information.** 137 of the 287 Miracles are called exactly
   "Ordinary Miracle", "Rare Miracle" or "Extraordinary Miracle". The effect text
   is the only thing that distinguishes them, so everything here reads the
   effect and nothing reads the name.

2. **Most Miracles pay out through the Domain deck, not immediately.** Adding
   Domains, raising their level, attaching beacons and deleting cards all only
   pay off over the Domains you have *left to enter*. A Miracle that raises the
   level of 5 random Domains is excellent on Domain 2 and close to worthless on
   Domain 19. Immediate payloads — fragments, blessings, an Equation — behave the
   opposite way.

3. **Reshuffling is a real competitor, and its value is measurable.** The pool a
   Mask draws from is known, so instead of guessing what a redraw is worth, the
   expected best of a fresh hand is computed from that Mask's actual pool
   (`_expected_best`). Rerolling costs one of a limited number of resets, so the
   engine weighs the improvement against having one fewer reset for the rest of
   the run.

Everything is scored from the resolved effect text. The room references
(`#{room_comp_type:3}`) are joined into real Domain and beacon names at build
time, which is also what lets a named beacon be scored by its real polarity
rather than by guessing from the wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from engine import dataset, economy, equations
from engine.state import RunState

# Weights, in the same spirit as engine/masks.py: a Miracle score lands roughly
# in the 0-110 band and is only meaningful relative to the other Miracles in the
# same hand. These are estimates and have not been validated against a real
# clear — see the "Unvalidated" section of NOTES.md.
WEIGHTS = {
    "payload": 46,      # what it actually gives you, discounted by run position
    "timing": 14,       # explains that discount rather than reapplying it
    "build_fit": 22,    # does it feed the Equation you are actually chasing
    "mask_fit": 12,     # Core lines and pool membership
    "downside": 16,     # stated costs, applied negative
}

# Base worth of each effect class, 0..1, before magnitude and position.
# "deck" effects are paid out over the Domains you still have to enter;
# "now" effects land immediately.
PAYLOAD = {
    "equation":        (0.95, "now"),
    "equation_fuel":   (0.90, "now"),
    "blessing":        (0.55, "now"),
    "fragments":       (0.45, "now"),
    "curio":           (0.30, "now"),
    "domain_level":    (0.55, "deck"),
    "domain_add":      (0.45, "deck"),
    "domain_dup":      (0.50, "deck"),
    "beacon":          (0.45, "deck"),
    "domain_convert":  (0.32, "deck"),
    "domain_delete":   (0.30, "deck"),
    "redraw":          (0.28, "deck"),
    "mask_line":       (0.70, "deck"),
}

# A Miracle that hands you 350 fragments is at the top of what the pool offers.
FRAGMENT_CEILING = 450

# Deck effects need this many Domains still to come to pay out in full.
DECK_HORIZON = 8


@dataclass
class Effect:
    """One thing a Miracle does, read out of its own text."""
    kind: str
    magnitude: float = 1.0      # multiplier on the base worth
    note: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "magnitude": round(self.magnitude, 2), "note": self.note}


# --------------------------------------------------------------------- reading

def _int(m: re.Match | None, group: int = 1, default: int = 1) -> int:
    return int(m.group(group)) if m else default


def _level_change(low: str) -> tuple[int, int]:
    """(how many Domains, how many levels) from a Domain-level Miracle.

    The pool phrases the same thing three ways — "increases the level of 2 random
    Domain(s) by 1", "Designate 1 Combat Domain(s), increasing their level by 3",
    "Designate 1 Domain to increase its level by 2" — so all three are read here
    rather than scattered through the classifier.
    """
    m = re.search(r"level of (?:(all)|(\d+)) [^.]*? by (\d+)", low)
    if m:
        # "all Combat and Elite Domains" — the deck holds several of each, and 4
        # is a deliberately conservative stand-in for "several".
        return (4 if m.group(1) else int(m.group(2))), int(m.group(3))

    m = re.search(r"increas\w*\s+(?:their|its)\s+level by (\d+)", low)
    if m:
        c = re.search(r"designate (\d+)", low)
        return _int(c), int(m.group(1))

    return 0, 0


def classify(miracle: dict) -> list[Effect]:
    """Read a Miracle's effect text into the things it does.

    Deliberately reads the text rather than keying off ids: the pool is
    regenerated every patch and the ids move, but the sentence shapes ("Obtain N
    Domain(s)", "increasing their level by N") are stable and are what a player
    reads too.
    """
    text = miracle.get("effect", "")
    low = text.lower()
    out: list[Effect] = []

    # --- immediate payloads ------------------------------------------------
    m = re.search(r"(\d+)\s+cosmic fragment", low)
    if m:
        amount = int(m.group(1))
        out.append(Effect("fragments", min(1.5, amount / FRAGMENT_CEILING),
                          f"{amount} Cosmic Fragments"))

    if "required for the equation" in low:
        m = re.search(r"(\d+)\s+(?:random\s+)?blessing", low)
        n = _int(m)
        out.append(Effect("equation_fuel", min(1.5, n / 2.0),
                          f"{n} blessing(s) chosen to fit your Equation"))
    elif re.search(r"blessing\(s\)|random blessing", low):
        m = re.search(r"(\d+)\s+(?:random\s+)?blessing", low)
        n = _int(m)
        high = "2- to 3-star" in low or "3-star" in low
        out.append(Effect("blessing", min(1.5, n / 2.0) * (1.25 if high else 1.0),
                          f"{n} blessing(s)" + (" at 2-3 star" if high else "")))

    m = re.search(r"equation of (\d+)-star", low)
    if m:
        stars = int(m.group(1))
        out.append(Effect("equation", 0.75 + 0.25 * stars, f"a {stars}-star Equation"))

    if re.search(r"curio\(s\)|curio of", low):
        m = re.search(r"(\d+)\s+(?:random\s+)?curio", low)
        out.append(Effect("curio", min(1.5, _int(m) / 1.5), f"{_int(m)} curio(s)"))

    # --- Domain deck -------------------------------------------------------
    m = re.search(r"(?:obtain|adds|gain)\s+(\d+)\s+(?:random\s+)?(?:lv\. \d+ )?[\"\w: -]*domain", low)
    # "Adds 1 random beacon to 3 random Domain(s)" reaches the word "Domain" too,
    # but it adds no Domain — it is a beacon Miracle and is counted as one below.
    if m and "beacon" in m.group(0):
        m = None
    if m and "duplicate" not in low:
        n = int(m.group(1))
        # A Domain two levels above the Plane is worth clearly more than one at
        # Plane level, and the pool prices exactly that difference.
        boost = 1.0
        if "lv. 5" in low:
            boost = 1.5
        elif "level 2 higher" in low:
            boost = 1.35
        elif "level 1 higher" in low:
            boost = 1.15
        out.append(Effect("domain_add", min(2.0, n * 0.55 * boost),
                          f"{n} extra Domain(s) in the deck"))

    if "duplicate" in low:
        m = re.search(r"(\d+)\s+duplicate", low)
        out.append(Effect("domain_dup", _int(m), "duplicates a Domain you choose"))

    count, steps = _level_change(low)
    if steps:
        out.append(Effect("domain_level", min(2.5, count * steps * 0.30),
                          f"+{steps} level on {count} Domain(s)"))

    if re.search(r"delete[s]? \d+ designated domain", low):
        m = re.search(r"delete[s]? (\d+) designated", low)
        out.append(Effect("domain_delete", _int(m),
                          "thins the deck of Domains you do not want"))

    if "to become" in low and "domain" in low:
        m = re.search(r"designate (\d+)", low)
        out.append(Effect("domain_convert", _int(m), "converts Domain types"))

    if "redraw" in low and "cannot redraw" not in low:
        out.append(Effect("redraw", 1.0, "more control over the Draw phase"))

    # --- beacons -----------------------------------------------------------
    if "beacon" in low:
        named = [dataset.get("beacon", b) for b in miracle.get("beacon_refs", [])]
        named = [b for b in named if b]
        # "attach 3 random beacons to it" counts beacons; "to 2 random Domain(s)"
        # counts Domains. Both are a multiplier on one beacon's worth, but the
        # beacon count has to be read first or the Domain count masks it.
        m = re.search(r"attach\w*\s+(\d+)\s+random beacon", low) \
            or re.search(r"to (\d+) (?:random|designated)", low)
        count = _int(m)
        if named:
            best = max(named, key=_beacon_worth)
            out.append(Effect("beacon", count * _beacon_worth(best),
                              f"attaches {best['name']}: {best['effect'][:80]}"))
        elif "negative" not in low:
            out.append(Effect("beacon", count * 0.8, f"{count} random beacon(s)"))
        else:
            out.append(Effect("beacon", count * 0.6, "random beacons, negatives included"))

    if not out:
        out.append(Effect("mask_line", 1.0, "no number to work from, so read the text"))

    return out


# What a Miracle makes you do *after* you take it. 89 of the 287 hand you a
# second decision — the "Select Waypoint Pass" screen, where you designate a card
# out of your whole draw pile — and that choice can matter more than the Miracle
# did. Blanking your Lv3 Elite carrying a Blessing beacon is a disaster wearing
# the same words as blanking a Lv1 Wealth.
TARGET_ACTIONS = [
    # (pattern, action, intent, what it does to the card)
    (r'become a "?blank"?', "blank", "sacrifice",
     "you destroy the Domain, and its level and every beacon on it go with it"),
    (r"delete[sd]? \d* ?designated", "delete", "sacrifice",
     "you remove the Domain from the deck, along with its level and beacons"),
    (r"to become", "convert", "sacrifice",
     "the Domain changes type, though it keeps its level"),
    (r"duplicate", "duplicate", "invest", "you get a second copy of the card"),
    (r"beacon", "beacon", "invest", "the card you choose gains a beacon"),
    (r"level", "level", "invest", "the card you choose gains levels"),
]


def targeting(miracle: dict) -> dict | None:
    """The follow-up choice this Miracle forces, if any.

    Returns the *intent* rather than just the verb, because that is what decides
    which end of the ranking you want: a sacrifice should land on your least
    valuable card, an investment on your best one, and getting that backwards is
    the whole risk.
    """
    text = miracle.get("effect", "")
    low = text.lower()
    if "designat" not in low:
        return None

    for pattern, action, intent, consequence in TARGET_ACTIONS:
        if re.search(pattern, low):
            m = re.search(r"designate (\d+)", low)
            return {
                "action": action,
                "intent": intent,
                "count": int(m.group(1)) if m else 1,
                "consequence": consequence,
                "restricted_to": _target_restriction(miracle),
            }
    return None


def _target_restriction(miracle: dict) -> list[str]:
    """Domain types the designation is limited to, if it is limited at all.

    The quoted names in a Miracle are a mix of what you may pick and what the
    card turns into — "Designate 1 Combat Domain(s) to become Occurrence" limits
    you to Combat and produces Occurrence. Everything after "to become" is an
    outcome, so only the part before it can restrict the choice.
    """
    text = miracle.get("effect", "")
    head = re.split(r"to become", text, flags=re.I)[0]
    names = {d["name"] for d in dataset.load()["domains"]}
    return [n for n in re.findall(r'"([^"]+)"', head) if n in names]


def _beacon_worth(beacon: dict) -> float:
    """Worth of one named beacon, from its own classified effects."""
    if beacon.get("polarity") == "Negative":
        return -0.9
    effects = set(beacon.get("effects", []))
    if "gain_equation" in effects:
        return 1.3
    if "gain_blessing" in effects:
        return 1.1
    if "gain_curio" in effects:
        return 0.9
    if "gain_fragments" in effects:
        return 0.8
    return 0.7


def downsides(miracle: dict) -> tuple[float, list[str]]:
    """Costs the Miracle states about itself, 0..1 with reasons."""
    low = miracle.get("effect", "").lower()
    penalty, notes = 0.0, []

    if "negative beacon" in low:
        penalty += 0.45
        notes.append("attaches a negative beacon")
    elif "negative beacons may appear" in low:
        penalty += 0.25
        notes.append("negative beacons can turn up")
    if "cannot redraw" in low:
        penalty += 0.55
        notes.append("gives up redrawing for the rest of the run")
    if "resets the mask's level" in low:
        penalty += 0.50
        notes.append("resets your Mask level")
    if "1 fewer option" in low:
        penalty += 0.30
        notes.append("one fewer option every future Miracle choice")
    if '"blank" domain' in low:
        penalty += 0.20
        notes.append("burns a Domain to a Blank")
    if re.search(r"delete[s]? \d+ designated domain", low) and "transfer" not in low:
        penalty += 0.05
        notes.append("costs you the Domain you delete")

    named_negative = [b for b in (dataset.get("beacon", i)
                                  for i in miracle.get("beacon_refs", []))
                      if b and b.get("polarity") == "Negative"]
    for b in named_negative:
        penalty += 0.35
        notes.append(f"{b['name']} is a negative beacon")

    return min(1.0, penalty), notes


# --------------------------------------------------------------------- scoring

@dataclass
class MiracleScore:
    miracle: dict
    factors: list = field(default_factory=list)
    effects: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    recommended: bool = False

    @property
    def total(self) -> float:
        return sum(f["points"] for f in self.factors)

    def to_dict(self) -> dict:
        return {
            "id": self.miracle["id"],
            "kind": "miracle",
            "name": self.miracle["name"],
            "effect": self.miracle["effect"],
            "rarity": self.miracle["rarity"],
            "universal": self.miracle.get("universal", False),
            "targeting": targeting(self.miracle),
            "score": round(self.total, 1),
            "factors": self.factors,
            "effects": [e.to_dict() for e in self.effects],
            "warnings": self.warnings,
            "recommended": self.recommended,
        }


def _factor(name: str, raw: float, weight: float, note: str) -> dict:
    return {"name": name, "raw": round(raw, 3), "weight": weight,
            "points": round(raw * weight, 2), "note": note}


@lru_cache(maxsize=64)
def _track_for(counts_key: tuple, picks: int):
    tracks = equations.reachable(dict(counts_key), picks, include_active=False)
    return tracks[0] if tracks else None


def _best_track(run: RunState):
    """The Equation this run is actually chasing, if there is one.

    Cached on the Path counts because scoring a whole Mask pool for the reroll
    estimate asks this ~180 times with the same answer, and each miss walks all
    80 Equations.
    """
    return _track_for(tuple(sorted(run.path_counts().items())), run.picks_remaining())


def score_miracle(miracle: dict, run: RunState) -> MiracleScore:
    out = MiracleScore(miracle=miracle, effects=classify(miracle))

    # --- payload -----------------------------------------------------------
    # Deck value is discounted by how many Domains are left to spend it in. A
    # Miracle that raises the level of five Domains is worth what it is worth
    # *only if you still enter them*, so the discount belongs inside the payload
    # rather than being bolted on afterwards — otherwise a dead deck effect keeps
    # its full headline value and only loses a few points at the margin.
    left = run.domains_left()
    horizon = min(1.0, left / DECK_HORIZON)
    discount = 0.25 + 0.75 * horizon

    immediate, deck, notes = 0.0, 0.0, []
    for e in out.effects:
        base, when = PAYLOAD.get(e.kind, (0.3, "now"))
        contribution = base * e.magnitude
        if when == "deck":
            deck += contribution
        else:
            immediate += contribution
        if e.note:
            notes.append(e.note)

    payload_raw = min(1.4, immediate + deck * discount)
    out.factors.append(_factor("what it gives", payload_raw, WEIGHTS["payload"],
                               ", ".join(notes[:3]) or "no readable payload"))

    # --- timing ------------------------------------------------------------
    total = immediate + max(0.0, deck)
    deck_weight = (max(0.0, deck) / total) if total > 0 else 0.0
    timing_raw = deck_weight * horizon + (1 - deck_weight) * 0.85
    if deck_weight > 0.5 and horizon < 0.5:
        note = (f"only {left} Domain(s) left to enter, and this pays out through "
                f"the Domain deck")
    elif deck_weight > 0.5:
        note = f"{left} Domain(s) left for this to pay out over"
    else:
        note = "lands immediately, so position barely matters"
    out.factors.append(_factor("timing", timing_raw, WEIGHTS["timing"], note))

    # --- build fit ---------------------------------------------------------
    fit, fit_note = _build_fit(out.effects, run)
    out.factors.append(_factor("build fit", fit, WEIGHTS["build_fit"], fit_note))

    # --- mask fit ----------------------------------------------------------
    mask_raw, mask_note, warn = _mask_fit(miracle, run)
    out.factors.append(_factor("mask fit", mask_raw, WEIGHTS["mask_fit"], mask_note))
    if warn:
        out.warnings.append(warn)

    # --- stated downsides --------------------------------------------------
    penalty, dnotes = downsides(miracle)
    if penalty:
        out.factors.append(_factor("downside", -penalty, WEIGHTS["downside"],
                                   ", ".join(dnotes)))
    return out


def _build_fit(effects: list[Effect], run: RunState) -> tuple[float, str]:
    """Does this Miracle feed the Equation and the economy this run actually has?"""
    kinds = {e.kind for e in effects}
    track = _best_track(run)

    if "equation_fuel" in kinds:
        if track:
            return 1.0, (f"blessings picked for your Equation, and {track.equation['name']} "
                         f"is {track.distance} away")
        return 0.6, "targets your Equation, but you have no track going yet"

    if "equation" in kinds:
        return 0.9, "an Equation outright, which no amount of blessings guarantees"

    if "blessing" in kinds:
        if track:
            return 0.55, (f"random blessings. Some land on {track.equation['name']}, "
                          f"most do not")
        return 0.40, "random blessings, with no Equation track to aim them at"

    if "fragments" in kinds:
        scarcity = economy.fragment_scarcity(run)
        if scarcity <= 0.2:
            return 0.15, (f"you hold {run.fragments:,} fragments with {run.domains_left()} "
                          f"Domain(s) left, so more of them buys little")
        if scarcity >= 0.8:
            return 0.85, f"fragments are tight ({run.fragments:,} held)"
        return 0.5, f"{run.fragments:,} fragments held, so this is useful but not urgent"

    if kinds & {"domain_level", "domain_add", "beacon", "domain_dup"}:
        return 0.55, "richer Domains mean more blessing and curio offers, whatever you are building"

    if "mask_line" in kinds:
        return 0.5, "a Mask-specific line, worth whatever your way of playing makes it"

    return 0.4, "no direct effect on your Equation or your economy"


def _mask_fit(miracle: dict, run: RunState) -> tuple[float, str, str]:
    """Core lines belong to one Mask; the rest are shared or universal."""
    mask = run.mask()
    ids = miracle.get("mask_ids") or []

    if miracle["rarity"] == "Core":
        if mask and mask["id"] in ids:
            return 1.0, f"a Core line for {mask['name']}, what the Mask is built around", ""
        if mask and ids:
            owner = ", ".join(m["name"] for m in
                              (dataset.get("mask", i) for i in ids) if m)
            return 0.0, f"a Core line for {owner}, not {mask['name']}", (
                f"This Core Miracle belongs to {owner}. If the game offered it to you, "
                f"the Mask on the Setup tab is probably wrong."
            )
        return 0.75, "a Core line, the strongest tier the pool has", ""

    if not mask:
        return 0.5, "no Mask set, so pool membership cannot be checked", ""
    if not ids:
        return 0.6, "universal, so every Mask can draw it", ""
    if mask["id"] in ids:
        return 0.7, f"in {mask['name']}'s own pool", ""
    return 0.3, f"not in {mask['name']}'s pool", (
        f"This Miracle is not in {mask['name']}'s pool. Ordinary/Rare/Extraordinary "
        f"Miracles all share the same three names, so this could be the wrong row. "
        f"Check the effect text against what the game is showing you."
    )


# ------------------------------------------------------------------- reshuffle

def pool(run: RunState) -> list[dict]:
    """Every Miracle the current Mask can be offered."""
    gifts = dataset.load()["mask_gifts"]
    mask = run.mask()
    if not mask:
        return list(gifts)
    return [g for g in gifts if not g.get("mask_ids") or mask["id"] in g["mask_ids"]]


def _expected_best(run: RunState, hand_size: int, rarities: list[str]) -> tuple[float, int]:
    """Expected best score of a fresh hand, from the Mask's real pool.

    Exact rather than sampled: for a hand of `n` drawn from an empirical
    distribution of `m` scored Miracles, the expected maximum is
    `sum(v_i * ((i/m)**n - ((i-1)/m)**n))` over the sorted scores. That makes the
    reroll advice reproducible instead of a different number every click.
    """
    candidates = pool(run)
    wanted = set(rarities)
    if wanted:
        matching = [g for g in candidates if g["rarity"] in wanted]
        if len(matching) >= hand_size:
            candidates = matching
    if not candidates:
        return 0.0, 0

    scores = sorted(score_miracle(g, run).total for g in candidates)
    m = len(scores)
    n = max(1, hand_size)
    expected = sum(v * (((i + 1) / m) ** n - (i / m) ** n) for i, v in enumerate(scores))
    return expected, m


def reshuffle_verdict(offered: list[MiracleScore], run: RunState,
                      resets_remaining: int) -> dict:
    """Rerolling the hand, scored on the same scale as the Miracles themselves."""
    best = max((s.total for s in offered), default=0.0)
    hand = max(1, len(offered))
    rarities = [s.miracle["rarity"] for s in offered]
    expected, sampled = _expected_best(run, hand, rarities)

    reasons = []
    if sampled:
        reasons.append(
            f"a fresh hand of {hand} from this Mask's pool averages {expected:.0f} "
            f"(from {sampled} Miracles it can offer), against {best:.0f} in front of you"
        )

    if resets_remaining <= 0:
        return {
            # No score rather than a sentinel: -inf is not valid JSON and a large
            # negative number would still render as a ranked option.
            "action": "reshuffle", "target": "Reshuffle the hand",
            "score": None, "available": False,
            "expected": round(expected, 1), "resets_remaining": 0,
            "reasons": ["No resets left. The run grants a limited number and you "
                        "have used them all."],
            "recommended": False,
        }

    # Spending a reset costs you the option of rerolling a later, possibly worse,
    # hand. That option is worth more the more run is left and the fewer resets
    # you hold.
    progress_left = run.domains_left() / max(1, run.domain_total)
    opportunity = 6.0 * progress_left / resets_remaining
    reasons.append(
        f"{resets_remaining} reset(s) left with {run.domains_left()} Domain(s) to go, so "
        f"spending one now costs about {opportunity:.0f} point(s) of later flexibility"
    )

    return {
        "action": "reshuffle", "target": "Reshuffle the hand",
        "score": round(expected - best - opportunity, 1), "available": True,
        "expected": round(expected, 1), "resets_remaining": resets_remaining,
        "reasons": reasons, "recommended": False,
    }


# ------------------------------------------------------------------------ rank

def rank(miracle_ids: list[int], run: RunState, resets_remaining: int = 0) -> dict:
    """Rank the Miracles on offer, with reshuffling as a ranked competitor."""
    offered = [m for m in (dataset.get("miracle", i) for i in miracle_ids) if m]
    scored = sorted((score_miracle(m, run) for m in offered), key=lambda s: -s.total)

    reshuffle = reshuffle_verdict(scored, run, resets_remaining)
    take_best = scored[0].total if scored else 0.0

    if scored and not (reshuffle["available"] and reshuffle["score"] > 0):
        scored[0].recommended = True
    elif reshuffle["available"] and reshuffle["score"] > 0:
        reshuffle["recommended"] = True

    warnings: list[str] = []
    for s in scored:
        warnings.extend(s.warnings)

    if len(scored) >= 2 and take_best > 0:
        margin = (scored[0].total - scored[1].total) / abs(take_best)
        if margin < 0.08:
            warnings.append(
                f"Close call: the top two are within 8%. Either one is defensible, so "
                f"take whichever fits how you are playing the deck."
            )

    if run.domains_left() <= 3 and scored:
        deck_heavy = [s for s in scored
                      if any(PAYLOAD.get(e.kind, ("", "now"))[1] == "deck" for e in s.effects)]
        if deck_heavy:
            warnings.append(
                f"{run.domains_left()} Domain(s) left. Anything that improves the Domain "
                f"deck barely pays out now, so prefer one that hands you something."
            )

    if not run.mask_id:
        warnings.append(
            "No Mask set on the Setup tab, so pool membership and Core lines are not "
            "being checked and the reroll estimate covers every Mask's pool at once."
        )

    return {
        "results": [s.to_dict() for s in scored],
        "reshuffle": reshuffle,
        "warnings": warnings,
        "run": {
            "domain_index": run.domain_index,
            "domain_total": run.domain_total,
            "domains_left": run.domains_left(),
            "wishpower_level": run.wishpower_level,
            "endgame": run.endgame(),
        },
    }
