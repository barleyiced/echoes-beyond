"""Every number the engine values, read live out of the modules that use it.

This backs the *How it scores* tab. It exists because a recommendation you
cannot interrogate is one you cannot safely overrule, and overruling one is
sometimes right — the same reason `scoring.Scored` carries a per-factor
breakdown rather than a bare total.

Two rules shape the whole module:

1. **Nothing here is retyped.** Every figure is read from the constant the
   engine actually uses (`store.FLOOR`, `miracles.PAYLOAD`, `weights.yaml`, …).
   A tab that documented the weights by transcribing them would be wrong the
   first time anyone tuned one, and wrong in the most damaging way — confidently,
   and about exactly the thing the reader came here to check.

2. **Every group states where its numbers came from.** Three provenances:

   * ``data``     — read out of the pinned game files. Facts.
   * ``measured`` — computed from that data or observed on a real run.
   * ``estimate`` — a guess. Defensible in shape, unvalidated in magnitude.
     These are the ones listed under "Unvalidated" in NOTES.md, and the ones
     worth arguing with.

   Most of what makes a recommendation is `estimate`, and the tab says so rather
   than letting a tidy table imply a precision the tool does not have.

`snapshot()` adds what those formulas evaluate to *for the run you are actually
holding*, since "0.35 x need" means little until you can see that need is 0.82
right now because you are two blessings from an Equation.
"""

from __future__ import annotations

from engine import economy, equations, masks, miracles, scoring, store, waypoint
from engine.rating import BANDS
from engine.state import PICKS_PER_DOMAIN, PLANES, RunState

DATA = "data"
MEASURED = "measured"
ESTIMATE = "estimate"


def _table(caption: str, cols: list[str], rows: list[list], note: str = "") -> dict:
    return {"caption": caption, "cols": cols, "rows": rows, "note": note}


def _num(x: float) -> float:
    return round(float(x), 3)


def _label(key: str) -> str:
    """`domain_level` → `domain level`. The engine's keys are readable already;
    they just should not arrive on the page looking like code."""
    return key.replace("_", " ")


# --------------------------------------------------------------- the scorer

def _blessing_group() -> dict:
    cfg = scoring.weights()
    f = cfg["factors"]
    # What the 0..1 `raw` of each factor actually measures. Kept beside the
    # weights rather than in the page, so a new factor cannot be added to the
    # scorer and left undocumented in the one place claiming to document it.
    meaning = {
        "equation_progress": "the best Equation track this pick advances, ÷ 1.5 and capped at 1",
        "path_dilution": "negative: how far under the commitment floor this Path is × how much run is gone",
        "team_synergy": "matched mechanic tags ÷ 3, capped at 1, × element fit",
        "gate_fit": "0 if no character can trigger it, else 0.55 + 0.25 per matching character",
        "mask_synergy": "0.4 + 0.3 per tag shared with your Mask",
        "rarity": "the rarity value below, the only place the label counts for anything",
        "upgrade_headroom": "how much a Workbench enhance would add",
        "plane_urgency": "1 − progress for things that compound. 0.35 + 0.65 × progress for immediate power",
    }
    label = {
        "equation_progress": "equation progress", "path_dilution": "path focus",
        "team_synergy": "team synergy", "gate_fit": "team gate",
        "mask_synergy": "mask", "rarity": "base power",
        "upgrade_headroom": "upgrade headroom", "plane_urgency": "timing",
    }
    order = sorted(f, key=lambda k: -f[k])
    return {
        "key": "scorer",
        "title": "The blessing and curio scorer",
        "provenance": ESTIMATE,
        "source": "engine/weights.yaml, engine/scoring.py",
        "tables": [
            _table(
                "Factors, heaviest first. A card's score is the sum of raw × weight.",
                ["Factor", "Weight", "What its 0 to 1 raw measures"],
                [[label[k], f[k], meaning.get(k, "")] for k in order],
                f"Maximum if every factor scored 1: {sum(v for v in f.values() if v > 0)} points. "
                "No real card comes close. Path focus is a penalty and the rest pull against "
                "each other, which is the point.",
            ),
            _table(
                "Rarity value: what the label alone is worth.",
                ["Rarity", "Blessing", "Curio"],
                [[r,
                  cfg["rarity_value"]["blessing"].get(r, "n/a"),
                  cfg["rarity_value"]["curio"].get(r, "n/a")]
                 for r in ("Legendary", "Rare", "Common", "Negative")],
                "Multiplied by the base-power weight above, so a Legendary on a Path you have "
                "abandoned earns its rarity points and nothing else. That is the whole of what "
                "rarity buys.",
            ),
            _table(
                "Thresholds.",
                ["Threshold", "Value", "Effect"],
                [
                    ["commitment floor", cfg["thresholds"]["commitment_floor"],
                     "below this many blessings a Path counts as uncommitted, and adding to it dilutes the build"],
                    ["close-call margin", cfg["thresholds"]["close_call_margin"],
                     "when the top two land this close, you get a tie rather than a pick"],
                    ["min tags for confidence", cfg["thresholds"]["min_tags_for_confidence"],
                     "below this many readable tags, the card carries a weak-evidence warning"],
                ],
            ),
            _table(
                "Tag classes.",
                ["Class", "Tags", "Why"],
                [
                    ["scales over the run", ", ".join(cfg["scaling_tags"]),
                     "worth more the more Domains are left to compound in"],
                    ["generically good", ", ".join(cfg["generic_good_tags"]),
                     "count at a quarter rate, so 'increases DMG' cannot outscore the thing your team is built on"],
                ],
            ),
        ],
    }


def _equation_group() -> dict:
    return {
        "key": "equations",
        "title": "Equation progress",
        "provenance": ESTIMATE,
        "source": "engine/equations.py",
        "tables": [
            _table(
                "What each Equation tier is worth.",
                ["Tier", "Value"],
                [["Boundary (PathEcho)" if k == "PathEcho" else k, v]
                 for k, v in equations.RARITY_VALUE.items()],
                "Boundary Equations need 16 blessings of a single Path, which is most of a run.",
            ),
            _table(
                "How a track is valued: value × (0.35 + 0.65 × proximity) × (0.40 + 0.60 × budget).",
                ["Term", "Meaning"],
                [
                    ["value", "the tier value above"],
                    ["proximity", "1 − blessings still needed ÷ total the Equation requires"],
                    ["budget", "1 − blessings still needed ÷ picks you have left"],
                ],
                "Budget is what stops a 16-Nihility Boundary Equation on an empty run scoring "
                "like one you are two picks from finishing. Both are reachable. Only one is a plan.",
            ),
            _table(
                "Credit for a pick.",
                ["Term", "Value"],
                [
                    ["best track", "counted in full"],
                    ["second-best track", "counted at 0.15"],
                    ["everything else", "0"],
                ],
                "Summed instead of taken best first, this rewarded opening five distant Equations "
                "over advancing the one you committed to, which is precisely the dilution "
                "the tool exists to prevent.",
            ),
            _table(
                "An Equation you do not hold counts for less, and less as the run runs out.",
                ["Where you are", "Fraction of its value counted"],
                [["start of the run", "1.00"],
                 ["halfway", f"{equations.unheld_discount(0.5):.2f}"],
                 ["last Domain", f"{equations.UNHELD_FLOOR:.2f}"]],
                "Meeting an Equation's Path requirements is not holding it. Counting an unheld "
                "one in full let a pick score for 'completing' something the run had never "
                "acquired and, on the last Domain, never would. The floor is what concentration "
                "is worth on its own, since another Equation on the same Path may still arrive.",
            ),
        ],
    }


# --------------------------------------------------------------- position

def _position_group() -> dict:
    return {
        "key": "position",
        "title": "Where you are in the run",
        "provenance": MEASURED,
        "source": "engine/state.py",
        "tables": [
            _table(
                "Derived from the Domain counter, never from the Plane number.",
                ["Quantity", "Formula"],
                [
                    ["domains left", "domain total − domain index + 1, including the one you stand in"],
                    ["progress", "(domain index − 1) ÷ (domain total − 1), so 0.0 at the start and 1.0 at the last"],
                    ["picks remaining", f"domains left × {PICKS_PER_DOMAIN} blessing choices per Domain"],
                    ["endgame", "3 or fewer Domains left"],
                ],
                f"A run is {PLANES} Planes but 13, 17 or 20 Domains depending on difficulty, so the "
                "same Domain index means different things in different runs. Everything positional "
                "reads the counter the game shows you.",
            ),
        ],
    }


# --------------------------------------------------------------- currency

def _currency_group() -> dict:
    return {
        "key": "currency",
        "title": "Fragments, Heat, and the price of spending",
        "provenance": ESTIMATE,
        "source": "engine/economy.py, engine/store.py",
        "tables": [
            _table(
                "Fragment scarcity: how much one fragment is worth right now.",
                ["Term", "Value"],
                [
                    ["expected need", f"domains left after this one × {economy.TYPICAL_SPEND_PER_DOMAIN} fragments"],
                    ["scarcity", "expected need ÷ your balance, clamped to 0.05 to 1.5"],
                    ["endgame taper", "multiplied by domains-left ÷ 4 over the last 3 Domains"],
                ],
                "Fragments do not survive the run, so the closer the counter gets to its total the "
                "cheaper spending becomes. At 15/17, hoarding throws the balance away.",
            ),
            _table(
                "Heat is not a balance.",
                ["Fact", "Consequence"],
                [
                    ["Heat resets at every Workbench", "you waste whatever Heat you do not spend"],
                    ["so its opportunity cost is ~0", "the bar for spending it is 'does this help at all'"],
                    ["fragments persist", "the bar for spending them is real, and the store floor is it"],
                ],
                "The game states this itself: “Heat will be reset for every Workbench”.",
            ),
        ],
    }


def _store_group() -> dict:
    return {
        "key": "store",
        "title": "The shops",
        "provenance": ESTIMATE,
        "source": "engine/store.py",
        "tables": [
            _table(
                "A Curio's worth is read from its own effect text, not from its tags.",
                ["Effect", "Base worth", "Pays out"],
                [[_label(k), v[0], "over the Domains left" if v[1] == "deck" else "immediately"]
                 for k, v in sorted(store.PAYLOAD.items(), key=lambda kv: -kv[1][0])],
                "163 of 235 Curios are tagged 'economy' and nothing else, so the shared blessing "
                "scorer rates a whole shelf between 6 and 15 out of ~50 and cannot separate "
                "'gain a Blessing of your Path' from 'lose every fragment on a coin flip'.",
            ),
            _table(
                "Stated costs, subtracted.",
                ["Downside", "Cost"],
                sorted(([_label(k), v] for k, v in store.DOWNSIDE.items()), key=lambda r: -r[1]),
                "A loss quoted inside a 'when …,' clause is the trigger, not the price. "
                "“When losing a Blessing, gains 50 Cosmic Fragments” does not cost you a Blessing.",
            ),
            _table(
                "Then the multipliers.",
                ["Multiplier", "Range", "What it reads"],
                [
                    ["build fit", "0.45 to 1.00", "does this feed a Path you are actually committed to"],
                    ["uses", "≤ 1", "a consumable is worth less than a permanent"],
                    ["randomness", "≤ 1", "a random payout is worth less than a chosen one"],
                    ["gating", "≤ 1", "a condition you may not meet"],
                    ["deck scale", f"domains left ÷ {store.DECK_HORIZON}, capped at 1",
                     "deck-shaped effects need Domains left to pay out in"],
                ],
            ),
            _table(
                "The hard pass.",
                ["Constant", "Value", "Meaning"],
                [
                    ["floor", store.FLOOR,
                     "below this a card is not worth buying however cheap it is and however much you hold"],
                    ["endgame floor", _num(store.FLOOR * store.ENDGAME_FLOOR_SCALE),
                     "the floor drops when fragments are about to be destroyed too, but never to zero"],
                    ["margin", store.MARGIN,
                     "a buy this close to walking away resolves to walking away"],
                    ["blessing scale", store.BLESSING_SCALE,
                     "divides the blessing scorer's points onto the same 0 to 1 scale the floor is on"],
                ],
                "The floor is deliberately independent of your balance. Being able to afford "
                "something is not an argument for buying it, and a shop screen is built so that a "
                "full wallet reads as permission.",
            ),
        ],
    }


def _workbench_group() -> dict:
    return {
        "key": "workbench",
        "title": "The Workbench",
        "provenance": MEASURED,
        "source": "engine/economy.py",
        "tables": [
            _table(
                "Why the bench leads with a plan rather than a ranking.",
                ["Fact", "Consequence"],
                [
                    ["enhancing costs 1 / 2 / 3 Heat by rarity",
                     "with 3 Heat the real choice is one Legendary against three Commons"],
                    ["so the best single enhance is the wrong question",
                     "a ranked list read top-down gives wrong advice"],
                    ["enhancing one Blessing does not change what another is worth",
                     "the plan is solved as an exact knapsack, so it does not shift when you reopen the tab"],
                    ["every Blessing has exactly two levels",
                     "enhancing is one-shot, so anything already enhanced drops out of the candidates"],
                ],
                "Players report the Heat costs. Nothing datamined them, because no cost table "
                "exists anywhere in the game files, so you can edit them on the Spend tab.",
            ),
        ],
    }


# --------------------------------------------------------------- the rest

def _door_group() -> dict:
    return {
        "key": "doors",
        "title": "Doors",
        "provenance": ESTIMATE,
        "source": "engine/waypoint.py",
        "tables": [
            _table(
                "A door is worth the average of what it supplies, weighted by what the run is short of.",
                ["Domain", "Supplies"],
                [[k, ", ".join(sorted(v)) or "nothing on its own"]
                 for k, v in waypoint.SUPPLIES.items()],
            ),
            _table(
                "Beacons, added on top.",
                ["Beacon effect", "Worth", "Scaled by"],
                [[_label(k), v[0], v[1] or "nothing, so it is worth this whatever you need"]
                 for k, v in waypoint.BEACON_VALUE.items()]
                + [["unreadable", waypoint.UNREAD_BEACON, "nothing"],
                   ["any negative beacon", f"−(its worth) − {waypoint.NEGATIVE_BEACON_EXTRA}", "nothing"]],
                "Domain level adds 0.05 per level above 1. Wealth and Store are cut to 0.55× over "
                "the last 3 Domains, when income and shopping barely pay off.",
            ),
            _table(
                "Redraw is priced, not guessed.",
                ["Term", "How"],
                [
                    ["value of a fresh hand",
                     "exact order statistic: the expected best of N cards drawn from the deck's own random Domain pool"],
                    ["cost", "one fewer redraw for the rest of the run"],
                ],
                "It cannot see the Reserve Pile or the new cards' beacons, so the estimate runs "
                "low, and it says so on the card.",
            ),
        ],
    }


def _miracle_group() -> dict:
    return {
        "key": "miracles",
        "title": "Wishpower Miracles",
        "provenance": ESTIMATE,
        "source": "engine/miracles.py",
        "tables": [
            _table(
                "Factors.",
                ["Factor", "Weight"],
                sorted(([_label(k), v] for k, v in miracles.WEIGHTS.items()), key=lambda r: -r[1]),
                "136 of the 286 Miracles are called exactly “Ordinary Miracle”, “Rare Miracle” or "
                "“Extraordinary Miracle”. Nothing here reads the name, only the effect text.",
            ),
            _table(
                "What each effect is worth.",
                ["Effect", "Base worth", "Pays out"],
                [[_label(k), v[0], "over the Domains left" if v[1] == "deck" else "immediately"]
                 for k, v in sorted(miracles.PAYLOAD.items(), key=lambda kv: -kv[1][0])],
                f"Deck effects are multiplied by domains-left ÷ {miracles.DECK_HORIZON}, capped at 1. "
                "Raising the level of 5 random Domains is excellent on Domain 2 and near-worthless "
                "on Domain 19. An immediate payload behaves the opposite way.",
            ),
            _table(
                "Reshuffle.",
                ["Term", "How"],
                [
                    ["value of a fresh hand",
                     "exact order statistic over this Mask's real pool, so the number never wanders between clicks"],
                    ["cost", "one fewer reset for the rest of the run"],
                ],
            ),
        ],
    }


def _mask_group() -> dict:
    return {
        "key": "masks",
        "title": "Masks",
        "provenance": ESTIMATE,
        "source": "engine/masks.py",
        "tables": [
            _table(
                "Factors.",
                ["Factor", "Weight"],
                sorted(([_label(k), v] for k, v in masks.WEIGHTS.items()), key=lambda r: -r[1]),
                "Team fit is deliberately the smallest: 8 of the 9 Masks contain no combat "
                "vocabulary at all. They are economy items, and scoring them on your team would "
                "invent a distinction the game does not make.",
            ),
            _table(
                "How a Mask earns Wishpower, read from its own text.",
                ["Income model", "Reliability", "Why"],
                [[_label(k), v[1], v[2]] for k, v in
                 sorted(masks.INCOME_MODELS.items(), key=lambda kv: -kv[1][1])],
            ),
        ],
    }


def _weighted_group() -> dict:
    return {
        "key": "weighted",
        "title": "Weighted Curios",
        "provenance": DATA,
        "source": "engine/synergy.py, engine/owned.py",
        "tables": [
            _table(
                "The gate is a hard filter, not a factor.",
                ["Situation", "Fit"],
                [
                    ["no character matches the gate", "0, so it does nothing in a socket however good the line reads"],
                    ["team not set yet", "0.5, since not knowing is not a fault"],
                    ["matches", "0.55 + 0.25 per matching character, capped at 1"],
                ],
                "Weighted Curios are equipped, not held: the screen reads “Weighted Curios "
                "Equipped: 0/2”. Holding six is not holding six, so only what you mark equipped "
                "counts toward the run's strength.",
            ),
        ],
    }


def _rating_group() -> dict:
    cfg = scoring.weights()["rating"]
    return {
        "key": "rating",
        "title": "The run rating",
        "provenance": ESTIMATE,
        "source": "engine/rating.py",
        "tables": [
            _table(
                "Strength, out of 100, where 100 is roughly a strong finished run.",
                ["Factor", "Weight"],
                sorted(([_label(k), v] for k, v in cfg["factors"].items()), key=lambda r: -r[1]),
                "Drag is subtracted on top, so a bag full of dead weight can score below the sum "
                "of its parts. Each live Equation past the best counts for "
                f"{cfg['equation_decay']} of the one before it. A flat sum let a six-Path smear "
                "light up sixteen cheap Rares and beat a committed build.",
            ),
            _table(
                "What full marks look like.",
                ["Target", "Value"],
                [[_label(k), v] for k, v in cfg["targets"].items()],
            ),
            _table(
                "Pace bands, on strength ÷ the reference run's strength.",
                ["From", "Verdict"],
                [[f"{lo:.2f}", label] for lo, _key, label in BANDS],
                "The middle band is wide on purpose. The reference is a guess, so a 10% gap "
                "either way is noise and reporting it as a verdict would pretend to a precision "
                "this does not have.",
            ),
            _table(
                "The reference run: competent, not optimal.",
                ["Assumption", "Value"],
                [
                    ["blessings per Domain passed", PICKS_PER_DOMAIN],
                    ["share of picks on the committed Path", cfg["reference_focus"]],
                    ["share on the second Path", cfg["reference_second"]],
                    ["mean rarity value held", cfg["reference_rarity"]],
                    ["Equations picked up per Domain", cfg["reference_equations_per_domain"]],
                    ["curios per Domain", cfg["reference_curio_per_domain"]],
                    ["share of upgradable blessings enhanced", cfg["reference_enhanced_share"]],
                    ["Miracles per Domain", cfg["reference_miracles_per_domain"]],
                ],
                "Nothing in the recommendation engine reads the rating. A rating that fed back "
                "into advice would start recommending whatever made its own number go up.",
            ),
        ],
    }


def constants() -> dict:
    return {
        "groups": [
            _blessing_group(), _equation_group(), _position_group(),
            _currency_group(), _store_group(), _workbench_group(),
            _door_group(), _miracle_group(), _mask_group(),
            _weighted_group(), _rating_group(),
        ],
    }


# ------------------------------------------------------- against your run

def snapshot(run: RunState) -> dict:
    """What those formulas currently evaluate to, for the run being held.

    "0.35 × need" is an abstraction until you can see that need is 0.82 right
    now, because you are two blessings from an Equation.
    """
    cfg = scoring.weights()
    floor = cfg["thresholds"]["commitment_floor"]
    counts = run.path_counts()
    left = run.domains_left()
    p = run.progress()
    committed = sorted((path for path, n in counts.items() if n >= floor),
                       key=lambda x: -counts[x])

    return {
        "position": {
            "domain": f"{run.domain_index} / {run.domain_total}",
            "domains_left": left,
            "progress": _num(p),
            "picks_remaining": run.picks_remaining(),
            "endgame": run.endgame(),
        },
        "rows": [
            ["progress", f"{p:.2f}",
             "0.0 at the first Domain, 1.0 at the last. Scales the dilution penalty and the timing factor."],
            ["picks remaining", str(run.picks_remaining()),
             f"{left} Domain(s) left × {PICKS_PER_DOMAIN} choices. An Equation needing more than this is out of reach."],
            ["timing, compounding effects", f"{1.0 - p:.2f}",
             "raw value of the timing factor for anything that scales over the run"],
            ["timing, immediate power", f"{0.35 + 0.65 * p:.2f}",
             "raw value for anything that lands now"],
            ["deck scale", f"{min(1.0, left / store.DECK_HORIZON):.2f}",
             f"deck-shaped effects keep this share of their worth with {left} Domain(s) left"],
            ["fragment scarcity", f"{economy.fragment_scarcity(run):.2f}",
             f"with {run.fragments:,} fragments. 0.05 is free, 1.5 is desperate."],
            ["store floor", f"{store.buy_floor(run):.2f}",
             "usefulness a purchase must show before it can be recommended"
             + (", lowered because the run is nearly over" if run.endgame() else "")],
            ["committed Paths", ", ".join(f"{c} ({counts[c]})" for c in committed) or "none",
             f"a Path counts as committed at {floor} blessings. Below that, adding to it scores as dilution"],
        ],
    }


def payload(run: RunState) -> dict:
    out = constants()
    out["now"] = snapshot(run)
    return out
