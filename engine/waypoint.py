"""Which door should you take?

At a waypoint you choose between Domains: Combat, Occurrence, Wealth, Store,
Reward, Respite, Elite, Boss, and the hidden pink Escapades. Each may carry
beacon attributes (+100 Cosmic Fragments, +1 Blessing, a level increase).

The right door depends on what the run is short of *now*, and that changes
sharply with position. Early, Domains that compound — Wealth, Store, anything
raising future income — are worth more. At 15/17 they are close to worthless:
fragments you win with two Domains to go can barely be spent, whereas a Combat
Domain that completes an Equation still pays. This module scores that trade
explicitly rather than ranking Domain types by a fixed preference list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine import dataset, economy, equations
from engine.state import RunState

# What each Domain type mainly supplies. Keys are matched against the display
# names in the dataset (built from RoguePersonaRoomCompType).
SUPPLIES: dict[str, set[str]] = {
    "Combat": {"blessing"},
    "Elite": {"blessing", "curio", "gear"},
    "Boss": {"progress"},
    "Occurrence": {"choice", "mixed"},
    "Aberration": {"choice", "mixed", "risk"},
    "Wealth": {"fragments"},
    "Store": {"buy", "blessing", "curio"},
    "Reward": {"curio", "fragments"},
    "Adventure": {"mixed"},
    "Respite": {"enhance", "heal", "reset"},
    "Escapade: Chest": {"fragments", "curio"},
    "Escapade: Blessing": {"blessing"},
    "Escapade: Curio Synthesis": {"curio"},
    "Escapade: Forge": {"weighted"},
    "Escapade: Surprise Occurrence": {"choice", "mixed"},
    "Forge": {"weighted", "enhance"},
    "Conversion": {"reset", "enhance"},
    "Blank": set(),
}


@dataclass
class DoorScore:
    name: str
    score: float
    reasons: list[str] = field(default_factory=list)
    beacons: list[dict] = field(default_factory=list)
    # How many beacons the card carries, whether or not they were named. Two
    # "Combat Lv2" cards are not interchangeable if one has three beacons, so
    # this has to reach the UI even when the count is all we were given.
    beacon_count: int = 0
    level: int | None = None
    desc: str = ""
    recommended: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name, "score": round(self.score, 2),
            "reasons": self.reasons, "beacons": self.beacons,
            "beacon_count": self.beacon_count,
            "level": self.level, "desc": self.desc,
            "recommended": self.recommended,
        }


def _needs(run: RunState) -> dict[str, float]:
    """What this run is short of, 0..1 per category, given where it is."""
    counts = run.path_counts()
    picks = run.picks_remaining()
    progress = run.progress()
    left = run.domains_left()

    needs: dict[str, float] = {}

    # Blessings: how close is the best Equation you can still finish?
    reach = [s for s in equations.reachable(counts, picks, include_active=False)]
    if reach:
        best = max(reach, key=lambda s: s.value / max(1, s.distance))
        # Closer and more valuable => wanting blessings more urgently.
        needs["blessing"] = min(1.0, best.value * (1.0 / max(1, best.distance)) * 3.0)
    else:
        needs["blessing"] = 0.25

    # Fragments are only worth chasing while there is still time to spend them.
    scarcity = economy.fragment_scarcity(run)
    needs["fragments"] = min(1.0, scarcity) * (0.15 if left <= 2 else 1.0)
    needs["buy"] = needs["fragments"] * 0.5 + (0.5 if run.fragments > 150 else 0.0)

    # Enhancing is worth more once you actually hold things worth enhancing.
    upgradable = sum(1 for b in (dataset.get("blessing", i) for i in run.owned_blessings)
                     if b and b.get("max_level", 1) > 1)
    needs["enhance"] = min(1.0, upgradable / 8.0) * (0.4 + 0.6 * progress)

    needs["curio"] = 0.45
    needs["weighted"] = 0.5 if not run.owned_weighted else 0.2
    needs["mixed"] = 0.4
    needs["choice"] = 0.4
    needs["progress"] = 1.0 if left <= 1 else 0.2
    needs["heal"] = 0.35
    needs["reset"] = 0.2
    needs["gear"] = 0.3
    needs["risk"] = 0.0
    return needs


# What one beacon effect is worth, and which entry of `_needs()` scales it.
# A blank need means the beacon is worth the same whatever the run is short of —
# an Equation is an Equation. Held as data rather than as a chain of `if`s so
# the How it scores tab can print the real numbers instead of a paraphrase.
BEACON_VALUE: dict[str, tuple[float, str]] = {
    "gain_fragments": (0.30, "fragments"),
    "gain_blessing":  (0.35, "blessing"),
    "gain_curio":     (0.25, "curio"),
    "gain_equation":  (0.45, ""),
    "enhance":        (0.25, "enhance"),
    "heal":           (0.20, "heal"),
    "domain":         (0.15, ""),
}

# A beacon whose effects we could not read at all. Small and positive: the game
# does not attach them for nothing, but we cannot say what it is.
UNREAD_BEACON = 0.10

# A negative beacon costs its own worth and then some, since the card carries it
# into every fight in that Domain.
NEGATIVE_BEACON_EXTRA = 0.20


def _beacon_value(beacon: dict, run: RunState, needs: dict[str, float]) -> tuple[float, str]:
    """Worth of one attribute attached to a door."""
    effects = set(beacon.get("effects", []))
    polarity = beacon.get("polarity", "Positive")
    v = 0.0
    for effect, (base, need) in BEACON_VALUE.items():
        if effect in effects:
            v += base * (needs[need] if need else 1.0)
    if not effects:
        v += UNREAD_BEACON
    if str(polarity).lower().startswith("neg"):
        v = -abs(v) - NEGATIVE_BEACON_EXTRA
    return v, beacon.get("name", "")


def score_door(domain_name: str, run: RunState, beacon_ids: list[int] | None = None,
               level: int | None = None) -> DoorScore:
    needs = _needs(run)
    supplies = SUPPLIES.get(domain_name, {"mixed"})
    reasons: list[str] = []

    score = 0.0
    ranked: list[tuple[str, float]] = []
    for s in supplies:
        want = needs.get(s, 0.3)
        score += want
        ranked.append((s, want))
    score /= max(1, len(supplies))

    # Always say what this door offers and how much the run wants it. A verdict
    # with no stated reason is not something you can sensibly overrule.
    ranked.sort(key=lambda x: -x[1])
    if ranked:
        top, want = ranked[0]
        strength = "badly needed" if want >= 0.7 else "useful" if want >= 0.4 else "low priority"
        supply_txt = ", ".join(s for s, _ in ranked[:3])
        reasons.append(f"supplies {supply_txt}, {strength} right now ({int(want * 100)}%)")
    elif not supplies:
        reasons.append("this Domain supplies nothing on its own")

    # Position-sensitive commentary and adjustment.
    left = run.domains_left()
    if domain_name in ("Wealth", "Store") and left <= 3:
        score *= 0.55
        reasons.append(f"only {left} Domain(s) left, so income and shopping barely pay off now")
    if domain_name == "Respite" and run.heat <= 0 and left > 3:
        reasons.append("Respite also refills Heat for enhancing")
    if domain_name == "Boss" and left <= 1:
        reasons.append("this is the run's last step")

    # Beacons, carried with their real text so the UI can show the same line the
    # game shows in the bar above the cards — that is how you check you entered
    # the card you are actually looking at.
    beacons: list[dict] = []
    if beacon_ids:
        by_id = {b["id"]: b for b in dataset.load()["beacons"]}
        for bid in beacon_ids:
            b = by_id.get(bid)
            if not b:
                continue
            v, label = _beacon_value(b, run, needs)
            score += v
            beacons.append({
                "id": b["id"], "name": label, "effect": b.get("effect", ""),
                "polarity": b.get("polarity", "Positive"), "value": round(v, 2),
            })

    if level:
        # Higher-level Domains give more but hit harder; mild positive.
        score += 0.05 * (level - 1)
        reasons.append(f"Domain level {level}")

    domain = next((d for d in dataset.load()["domains"] if d["name"] == domain_name), None)
    return DoorScore(name=domain_name, score=score, reasons=reasons, beacons=beacons,
                     beacon_count=len(beacons), level=level,
                     desc=(domain or {}).get("desc", ""))


def _expected_draw(run: RunState, hand_size: int, level: int | None) -> tuple[float, int]:
    """Expected best door of a fresh hand, from the deck's own random Domain pool.

    `deck.random_types` comes from `RogueTournPersona_RandomCompList` — the Domain
    types the draw actually randomises over — so this is grounded in the game's
    own list rather than an invented average. Exact order statistic over that
    pool, like the Miracle reroll estimate, so the number does not wander between
    clicks.

    What it deliberately cannot see: which cards are actually left in your Reserve
    Pile, and what beacons a fresh draw would carry. Both push the real value of a
    redraw *up*, so this estimate is conservative, and `redraw_verdict` says so.
    """
    pool = (dataset.load().get("deck") or {}).get("random_types") or []
    if not pool:
        return 0.0, 0
    scores = sorted(score_door(name, run, None, level).score for name in pool)
    m, n = len(scores), max(1, hand_size)
    expected = sum(v * (((i + 1) / m) ** n - (i / m) ** n) for i, v in enumerate(scores))
    return expected, m


def redraw_verdict(scored: list[DoorScore], run: RunState, redraws_remaining: int,
                   hand_size: int, level: int | None) -> dict:
    """Redrawing the hand, scored on the same scale as the doors themselves.

    Redraw attempts are limited per run, so this is the same shape of decision as
    Skip on a shop screen or Reshuffle at a Wishpower level-up: a real competitor
    that has to earn its place, not a fallback.
    """
    best = max((d.score for d in scored), default=0.0)
    expected, pool_size = _expected_draw(run, hand_size, level)

    if redraws_remaining <= 0:
        return {
            "action": "redraw", "target": "Redraw the hand", "score": None,
            "available": False, "expected": round(expected, 2),
            "redraws_remaining": 0, "recommended": False,
            "reasons": ["No redraws left. Take the best of what is in front of you."],
        }

    reasons = [
        f"a fresh hand of {hand_size} from the deck's {pool_size} random Domain types "
        f"averages {expected:.2f}, against {best:.2f} in front of you",
        "the estimate ignores beacons on the new cards and cannot see your Reserve "
        "Pile, so it errs on the low side",
    ]

    # A redraw kept is a redraw available at a worse waypoint later.
    progress_left = run.domains_left() / max(1, run.domain_total)
    opportunity = 0.25 * progress_left / redraws_remaining
    reasons.append(
        f"{redraws_remaining} redraw(s) left with {run.domains_left()} Domain(s) to go, so "
        f"spending one now costs about {opportunity:.2f} of later flexibility"
    )

    return {
        "action": "redraw", "target": "Redraw the hand",
        "score": round(expected - best - opportunity, 2), "available": True,
        "expected": round(expected, 2), "redraws_remaining": redraws_remaining,
        "reasons": reasons, "recommended": False,
    }


def rank_doors(doors: list[dict], run: RunState,
               redraws_remaining: int | None = None) -> dict:
    """Rank the doors on offer, with redrawing as a ranked competitor.

    Each door is {'name': 'Combat', 'beacons': [ids], 'level': 2} — the same three
    things the game's own card shows.
    """
    scored = [score_door(d.get("name", ""), run, d.get("beacons"), d.get("level")) for d in doors]
    scored.sort(key=lambda d: -d.score)

    redraws = run.door_redraws if redraws_remaining is None else redraws_remaining
    levels = [d.get("level") for d in doors if d.get("level")]
    redraw = redraw_verdict(scored, run, redraws, len(doors) or 1,
                            max(levels) if levels else None)

    if redraw["available"] and redraw["score"] > 0:
        redraw["recommended"] = True
    elif scored:
        scored[0].recommended = True

    warnings = []
    advice = economy.spend_everything_advice(run)
    if advice:
        warnings.append(advice)
    if len(scored) >= 2 and scored[0].score > 0 and \
            (scored[0].score - scored[1].score) / abs(scored[0].score) < 0.08:
        warnings.append(f"Close call between {scored[0].name} and {scored[1].name}.")
    if doors and not any(d.get("beacons") for d in doors):
        warnings.append(
            "No beacons entered. The game shows them in the bar above the cards, and they "
            "are often the whole difference between two doors. A free Blessing or a level "
            "increase outweighs the Domain type on its own."
        )

    return {
        "doors": [d.to_dict() for d in scored],
        "redraw": redraw,
        "warnings": warnings,
        "position": {
            "domain": run.domain_index, "total": run.domain_total,
            "left": run.domains_left(), "endgame": run.endgame(),
            "fragment_scarcity": round(economy.fragment_scarcity(run), 2),
        },
        "needs": {k: round(v, 2) for k, v in _needs(run).items()},
    }


def domain_types() -> list[str]:
    return [d["name"] for d in dataset.load()["domains"]]


# --------------------------------------------------------------- designating

# A generic beacon, for when you tell us a card carries two beacons but not which
# two. Deliberately positive-leaning: most beacons are, and for the question that
# matters — "which card can I afford to lose" — undervaluing beacons is the
# dangerous direction.
GENERIC_BEACON = 0.22


def card_worth(card: dict, run: RunState) -> DoorScore:
    """What one card in your deck is worth to you right now.

    Identical to what the card would score as a door, because it is the same
    question: a card is worth what entering it would give you. That means level
    and beacons count, which is exactly what stops "blank a Domain" landing on
    your best one.
    """
    score = score_door(card.get("name", ""), run, card.get("beacons"), card.get("level"))
    extra = int(card.get("beacon_count") or 0)
    if extra and not card.get("beacons"):
        score.score += extra * GENERIC_BEACON
        score.beacon_count = extra
        score.reasons.append(
            f"{extra} beacon(s) on the card, counted at an average value since you "
            f"did not say which"
        )
    return score


def rank_targets(cards: list[dict], run: RunState, intent: str = "sacrifice",
                 restricted_to: list[str] | None = None) -> dict:
    """Which card to designate, given what the Miracle wants to do to it.

    The whole ranking flips on `intent`: a sacrifice (blank, delete, convert)
    wants your *least* valuable card and an investment (beacon, level, duplicate)
    wants your best. Same numbers, opposite ends — which is why the intent is read
    from the Miracle text rather than left to the reader.
    """
    allowed = [c for c in cards
               if not restricted_to or c.get("name") in restricted_to]
    scored = [(card_worth(c, run), c) for c in allowed]
    sacrifice = intent == "sacrifice"
    scored.sort(key=lambda t: t[0].score if sacrifice else -t[0].score)

    for s, _ in scored:
        s.recommended = False
    if scored:
        scored[0][0].recommended = True

    warnings: list[str] = []
    if restricted_to and len(allowed) < len(cards):
        warnings.append(
            f"You can only choose {', '.join(restricted_to)} Domains, so "
            f"{len(cards) - len(allowed)} of your cards are not eligible."
        )
    if not allowed:
        warnings.append(
            "None of the cards you entered can be designated for this Miracle."
            if cards else "No cards entered, so the ranking below goes by Domain type only."
        )
    if sacrifice and scored:
        best = max(scored, key=lambda t: t[0].score)[0]
        # Name it precisely enough to pick it out of a pile that holds three
        # cards of the same type: level and beacon count are what distinguish it.
        bits = [b for b in (f"Lv {best.level}" if best.level else "",
                            f"{best.beacon_count} beacon(s)" if best.beacon_count else "")
                if b]
        warnings.append(
            f"Whatever you do, not {best.name}" +
            (f" ({', '.join(bits)})" if bits else "") +
            ". It is the most valuable card here, and a sacrifice destroys its "
            "level and beacons along with it."
        )

    return {
        "intent": intent,
        "cards": [s.to_dict() for s, _ in scored],
        "warnings": warnings,
        "needs": {k: round(v, 2) for k, v in _needs(run).items()},
    }


def deck_types() -> list[str]:
    """Domain types that can actually be in the pile you designate from.

    From `RogueTournPersona_RandomCompList`. Boss, Respite and Conversion are
    *fixed* placements, not cards — offering "blank your Boss" as cheap because
    the run does not currently want progress would be nonsense advice about a
    card that is not there.
    """
    deck = (dataset.load().get("deck") or {}).get("random_types") or []
    return deck or domain_types()


def target_guidance(run: RunState, intent: str = "sacrifice",
                    restricted_to: list[str] | None = None,
                    action: str | None = None) -> dict:
    """The same answer by Domain *type*, for when you have not entered your deck.

    Enough to act on without typing a thirteen-card draw pile in: the types are
    ranked, and the tie-break within a type is stated as a rule rather than
    computed.
    """
    types = restricted_to or deck_types()
    cards = [{"name": t, "level": None, "beacons": []} for t in types]
    out = rank_targets(cards, run, intent, restricted_to)
    out["by_type"] = True
    out["tiebreak"] = (
        "Between two cards of the same type, sacrifice the one with the lowest "
        "level and fewest beacons."
        if intent == "sacrifice" else
        "Between two cards of the same type, invest in the one with the highest "
        "level, and prefer one you are actually likely to enter."
    )
    if action == "blank" and any(c["name"] == "Blank" for c in out["cards"]):
        out["warnings"].insert(0, (
            "A card that is already Blank scores lowest here but is the one target "
            "to avoid. Blanking it spends the Miracle and changes nothing."
        ))
    return out
