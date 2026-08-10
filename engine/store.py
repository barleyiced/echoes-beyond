"""The Curio Store, the Blessing Store, and any shelf that sells for fragments.

The Occurrence side of the Spend tab reads *dialogue lines* out of
`RogueDialogueOptionDisplay`. A store shelf is a different screen and a
different problem: the goods are named Curios with a price printed on the card,
so the question is not "what does this line do" but "is this specific Curio
worth 180 fragments to *this* run, right now".

Three things shape everything here.

**1. Skip is the default, not the fallback.** Every other spend surface ranks
skip alongside the rest and lets the best score win. That is right when the
currency is about to evaporate (Heat) and wrong at a store, where the shelf is
built to look tempting and a full wallet reads as permission. So a purchase has
to clear a **usefulness floor** before it can be recommended at all — a bar on
what the Curio does, deliberately independent of what it costs and of what you
can afford. Below the floor the answer is a hard pass and the verdict says
which of the two reasons it is: "this does nothing for your run" is a different
sentence from "you cannot afford it", and they send you looking for different
things. See `buy_floor`.

**2. Rarity is the price tag, not the value.** The store prices by rarity —
observed at 100 for a 1-star and 180 for a 2-star — so a Rare has to be nearly
twice as useful as a Common to be the better buy, exactly as at the Workbench.
Ranking on raw value would quietly recommend the expensive card every time.

**3. The tags cannot carry the Curio shelf.** 163 of the 235 Curios are tagged
`economy` and nothing else, so the shared blessing scorer rates every card on
that shelf between 6 and 15 out of ~50 and cannot tell "gain a Blessing of your
committed Path" from "lose every fragment you hold on a coin flip". Curios
therefore get their own reader, in the same spirit as `engine/miracles.py` — see
`classify`. **Blessings are the opposite case**: `scoring.score_entry` already
does the right thing for them, so the Blessing Store scales that score onto this
module's 0..1 scale rather than re-deriving anything. Which is why the Blessing
Store's verdicts are dominated by Equation progress and Path concentration —
the same two things its own top bar is showing you.

**4. Batch Select is not the same question as one card.** The Blessing Store
lets you buy a set, and a set of three Remembrance cards can complete an
Equation that none of them completes alone. `plan_shelf` therefore builds the
plan one card at a time, re-scoring what is left against the Path counts the
previous pick created — see the note there for why this is deliberately *not*
the exact knapsack `economy.plan_workbench` uses.

Everything below the datamined text is an estimate. The prices are reported
from play (no price table exists upstream at the pinned commit — checked the
same way the Heat costs were), which is why they live in `RunState.store_prices`
and are editable rather than hardcoded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from engine import dataset, economy, equations, scoring
from engine.state import RunState
from engine.synergy import synergy_score, team_tags

# `scoring.score_entry` returns points, not a 0..1 value, so the Blessing Store
# has to land on the same scale as the Curio reader for the floor to mean one
# thing. Measured against a real Difficulty 5 run at Domain 14: the 144
# Blessings score 9 to 54, median 30. Dividing by this puts the median draw
# comfortably over the floor and the bottom decile under it, which is the
# behaviour wanted — a typical Blessing is worth buying, a bad one is not.
BLESSING_SCALE = 55.0

# Value below which a purchase is a hard pass however cheap it looks and however
# much you are holding. This is the number that makes the store different from
# the Workbench: Heat evaporates so its floor is ~0, fragments do not.
FLOOR = 0.30

# ...except at the very end of the run, when fragments evaporate too. The floor
# drops rather than vanishing: a marginal Curio for the last two Domains does
# beat a balance that is about to be deleted, but junk is still junk.
ENDGAME_FLOOR_SCALE = 0.55

# How far a buy must beat doing nothing before it is *recommended* rather than
# merely positive. Inside this band the tool says "defensible, but hold" — a
# near-tie at a store resolves to keeping the fragments.
MARGIN = 0.10

# Domains a deck-shaped effect (Domain levels, Domain types, biased future
# offers) needs still to come to pay out in full. Same constant and same reason
# as miracles.DECK_HORIZON.
DECK_HORIZON = 8

# A Curio handing over this many fragments is at the top of what the pool gives.
FRAGMENT_CEILING = 400

# Base worth of each effect class, 0..1, before magnitude, run position and
# build fit. "deck" effects pay out over the Domains you have left to enter;
# "now" effects land the moment you buy.
PAYLOAD: dict[str, tuple[float, str]] = {
    "path_blessing":   (0.60, "now"),    # "randomly gains 1 Blessing(s) of Elation"
    "path_bias":       (0.50, "deck"),   # "greatly increased chance for Blessings of X"
    "blessing":        (0.45, "now"),
    "equation":        (0.90, "now"),
    "curio":           (0.22, "now"),
    "fragments":       (0.30, "now"),
    "combat":          (0.50, "now"),    # a real stat line, read from the tags
    "heal":            (0.12, "now"),
    "domain_deck":     (0.45, "deck"),   # Domain level / type manipulation
    "blessing_choice": (0.25, "deck"),   # more (or fewer) cards on the pick screen
    "reroll":          (0.25, "deck"),
    "upgrade":         (0.40, "now"),
}

# Stated costs, subtracted. `fragment_wipe` is scaled by the balance you are
# actually holding rather than being a flat number — see `_downside`.
DOWNSIDE = {
    "fragment_wipe": 0.90,
    "hp_risk":       0.30,
    "lose_blessing": 0.40,
    "lose_curio":    0.25,
    "lose_fragments": 0.35,
}


@dataclass
class Effect:
    """One thing a Curio does, read out of its own text."""
    kind: str
    magnitude: float = 1.0
    note: str = ""
    path: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "magnitude": round(self.magnitude, 2),
                "note": self.note, "path": self.path}


# ---------------------------------------------------------------------- reading

_PATHS = ("Remembrance", "Nihility", "The Hunt", "Destruction", "Elation",
          "Propagation", "Erudition", "Harmony", "Preservation", "Abundance",
          "Trailblaze")

# Tags that mean "this Curio actually does something in a fight", as opposed to
# the `economy` tag that 163 of 235 carry and which says almost nothing.
COMBAT_TAGS = {"dmg_bonus", "crit", "atk", "spd", "break", "aoe", "vulnerability",
               "res_pen", "additional_dmg", "dot", "shield", "def_stat",
               "effect_res", "energy", "sp_positive", "ultimate", "summon",
               "basic_atk", "skill", "debuff"}


def _first_int(pattern: str, text: str, default: int = 1) -> int:
    m = re.search(pattern, text, re.I)
    return int(m.group(1)) if m else default


# The same trap `miracles._target_restriction` exists for: a loss can be quoted
# as the *condition* rather than as the price. "When losing a Blessing, gains 50
# Cosmic Fragments" does not cost you a Blessing — it pays out if one goes, and
# reading it as a cost turns a mild Curio into the worst card on the shelf.
_TRIGGER_WORDS = r"(?:when|whenever|upon|after|each time|every time|if)\b"


def _is_trigger(low: str, at: int) -> bool:
    """True when the phrase at `at` sits inside a 'when …,' antecedent."""
    clause_start = max(low.rfind(",", 0, at), low.rfind(".", 0, at)) + 1
    head = low[clause_start:at]
    return bool(re.match(rf"\s*{_TRIGGER_WORDS}", head)) and "," not in head


# Triggers that fire as a matter of course, so a payload behind one is worth
# close to its face value. Anything else gated on a condition is discounted and
# told why, rather than being guessed at.
_COMMON_TRIGGER = re.compile(
    r"when entering (?:combat|a|the)|after winning|at the (?:start|end) of|"
    r"after (?:destroying|entering|obtaining)|when using|"
    # "Upon gaining this Curio" is not a condition at all — it fires the moment
    # you pay for it, and reading it as one discounted the card for being free.
    r"upon (?:gaining|obtaining|acquiring) this", re.I)


def _gating(entry: dict) -> tuple[float, str]:
    """Discount for a Curio whose payoff only fires on an uncommon event."""
    low = (entry.get("desc", "") or "").lower()
    m = re.match(rf"\s*{_TRIGGER_WORDS}(.*?),", low)
    if not m or _COMMON_TRIGGER.search(low):
        return 1.0, ""
    return 0.55, (f"only pays out \"{m.group(0).rstrip(',').strip()}\". The data cannot tell "
                  f"you how often that happens, so it is worth whatever you judge that to be")


def _path_in(text: str) -> str:
    """The Path a Curio names, if it names one."""
    for p in _PATHS:
        if re.search(rf"\b{re.escape(p)}\b", text):
            return p
    return ""


def classify(entry: dict) -> list[Effect]:
    """Read a Curio's effect text into the things it does.

    Grounded in the phrasings that actually occur in the 235-Curio pool rather
    than in a general taxonomy: 17 Curios name a Path, 26 are destroyed after a
    number of uses, 32 turn on a chance, 11 hand over fragments, 8 touch Domain
    levels. Anything this cannot read falls through to the tags, and if those
    say nothing either the caller is told so in words rather than being handed a
    confident-looking zero.
    """
    text = entry.get("desc", "") or ""
    low = text.lower()
    tags = set(entry.get("tags", []))
    out: list[Effect] = []

    # --- Blessings, and above all *which Path's* ---------------------------
    path = _path_in(text)
    if re.search(r"gains? \d+ blessing\(?s?\)? of|obtain\w* \d+ blessing\(?s?\)? of", low):
        n = _first_int(r"gains? (\d+) blessing", low)
        out.append(Effect("path_blessing", min(2.0, n), f"{n} Blessing(s) of {path or 'a Path'}",
                          path))
    if re.search(r"(increased|higher) chance for blessings of", low):
        boost = 1.3 if "greatly" in low else 1.0
        out.append(Effect("path_bias", boost,
                          f"biases later Blessing offers toward {path or 'a Path'}", path))
    if re.search(r"extra blessing|additional blessing", low):
        n = _first_int(r"(\d+) extra blessing", low)
        # These almost always take something back in the same sentence, which is
        # why this is worth a quarter of a Blessing rather than a whole one.
        reduced = bool(re.search(r"reduc\w+ by \d+|will be reduced", low))
        out.append(Effect("blessing_choice", 0.5 if reduced else 1.0,
                          f"{n} extra Blessing on the pick screen"
                          + (", but one fewer to pick from" if reduced else "")))
    elif re.search(r"\bblessing\(s\)\b.*\bgain\b|gains? \d+ blessing", low) and not path:
        out.append(Effect("blessing", 1.0, "grants a Blessing"))

    if re.search(r"\bequation|formula\b", low) and re.search(r"gain|obtain", low):
        out.append(Effect("equation", 1.0, "grants an Equation"))

    # --- Fragments, in both directions -------------------------------------
    if re.search(r"loses all cosmic fragments", low):
        out.append(Effect("fragment_wipe", 1.0, "takes every fragment you are holding"))
    elif re.search(r"los\w+ \d+ cosmic fragment", low):
        n = _first_int(r"los\w+ (\d+) cosmic fragment", low)
        out.append(Effect("lose_fragments", min(1.5, n / 200), f"costs {n} fragments"))
    if re.search(r"gains? (?:\d+|cosmic fragments equal)", low) and "cosmic fragment" in low:
        n = _first_int(r"gains? (\d+) cosmic fragment", low, default=0)
        if n:
            out.append(Effect("fragments", min(1.6, n / (FRAGMENT_CEILING / 3)),
                              f"{n} Cosmic Fragments"))

    # --- Other Curios -------------------------------------------------------
    if re.search(r"receiv\w+ \d+ curio|gains? \d+ curio|obtain\w* \d+ curio", low):
        n = _first_int(r"(\d+) curio", low)
        out.append(Effect("curio", min(2.0, n), f"{n} more Curio(s)"))

    # --- The Domain deck ----------------------------------------------------
    if re.search(r"level of \d+|increas\w+ the level|becomes? a[n]? .*domain|"
                 r"domain'?s? (?:type|level)", low):
        n = _first_int(r"by (\d+)", low)
        out.append(Effect("domain_deck", min(2.0, n),
                          "changes Domains you have not entered yet"))

    if "reroll" in tags or re.search(r"refresh|reroll|re-?draw", low):
        out.append(Effect("reroll", 1.0, "buys you another look at something"))

    if "upgrade" in tags or re.search(r"enhanc\w+|upgrad\w+", low):
        out.append(Effect("upgrade", 1.0, "improves what you already hold"))

    # --- Actual combat value, from the tags rather than the prose -----------
    combat = tags & COMBAT_TAGS
    if combat:
        out.append(Effect("combat", 1.0, ", ".join(sorted(combat))))
    if "heal" in tags or re.search(r"restor\w+ .*hp|heals?", low):
        out.append(Effect("heal", 1.0, "restores HP"))

    # --- What it takes back -------------------------------------------------
    # Each of these is only a cost when it is *not* the condition the Curio
    # triggers on. See _is_trigger.
    for pattern, kind, note in (
        (r"losing \d+% of (?:their|its) current hp|lose \d+% of", "hp_risk", ""),
        (r"los\w+ (?:1|a|one) blessing|destroy\w+ \d+ blessing", "lose_blessing",
         "costs you a Blessing"),
        (r"destroy\w+ (?:1|a|one|another) curio", "lose_curio", "costs you a Curio"),
    ):
        m = re.search(pattern, low)
        if not m or _is_trigger(low, m.start()):
            continue
        if kind == "hp_risk":
            pct = _first_int(r"(\d+)% of", low)
            out.append(Effect(kind, min(2.0, pct / 50), f"can cost {pct}% of current HP"))
        else:
            out.append(Effect(kind, 1.0, note))

    return out


def uses(entry: dict) -> tuple[float, str]:
    """How many times a Curio fires before the game takes it away, and why.

    26 of the pool are destroyed after a stated number of triggers or battles.
    A one-shot is not worth what a permanent is, and the store sells both at the
    same price, so this is a real part of the decision rather than flavour. Four
    Curios can be repaired with Technique Points, which is what makes a one-shot
    like Transform!!! behave more like a recurring one.
    """
    low = (entry.get("desc", "") or "").lower()
    if not re.search(r"destroy\w+ after|will be destroyed|is destroyed", low):
        return 1.0, ""
    n = 1
    m = re.search(r"(?:triggers?|total of) (\d+) (?:time|battle)", low)
    if m:
        n = int(m.group(1))
    repairable = bool(re.search(r"technique point", low))
    # Capped below 1.0 on purpose: however many charges it has, a Curio the game
    # takes away is not worth what one it does not is.
    factor = min(0.90, 0.40 + 0.15 * n)
    if repairable:
        factor = min(0.95, factor + 0.30)
        return factor, (f"the game destroys it after {n} use(s), but Technique Points "
                        f"repair it, so treat it as recurring")
    return factor, f"the game destroys it after {n} use(s), so it is not a permanent addition"


def randomness(entry: dict) -> tuple[float, str]:
    """Discount for a Curio whose payoff is a dice roll, with the reason.

    "Randomly gains 1 Blessing(s) of Elation" is not a dice roll on *whether* you
    get one — only on which — so a blanket discount on the word "random" would
    mark down the one card on the shelf that is a certainty.
    """
    low = (entry.get("desc", "") or "").lower()
    if re.search(r"small chance", low):
        return 0.55, "the good outcome is a \"small chance\", and the data does not give the odds"
    if re.search(r"randomly gains? \d+ blessing", low):
        return 1.0, ""
    if re.search(r"\bchance\b|randomly|\brandom\b", low):
        return 0.80, "the payoff is random, and the odds are not in the data"
    return 1.0, ""


# ---------------------------------------------------------------------- valuing

def _build_fit(effects: list[Effect], entry: dict, run: RunState) -> tuple[float, str]:
    """How much this Curio helps the build you are actually committed to, 0..1.

    A Path-flavoured Curio is the whole reason this exists: "gain a Blessing of
    Elation" is a completely different card on an Elation run than on a
    Destruction one, and it is the cheapest tier on the shelf either way.
    """
    counts = run.path_counts()
    floor = scoring.weights()["thresholds"]["commitment_floor"]
    for e in effects:
        if e.kind in ("path_blessing", "path_bias") and e.path:
            credit, drivers = equations.progress_credit(
                e.path, counts, run.picks_remaining(),
                owned=run.owned_equations, progress=run.progress())
            held = counts.get(e.path, 0)
            fit = min(1.0, 0.20 + 0.55 * min(1.0, credit / 1.5) + 0.05 * min(held, 5))

            # An Equation being *reachable* on a Path is not the same as the run
            # being on it — nearly every Path has something reachable, so keying
            # off the drivers alone rated an Elation card the same on a Hunt run.
            # Commitment is what separates them.
            if held < floor:
                fit *= 0.45
                return fit, (
                    f"you hold only {held} {e.path}. Buying into a Path you have not "
                    f"committed to is the dilution this tool exists to prevent")
            if drivers:
                d = drivers[0]
                feeds = d["name"] if d["held"] else f"{d['name']} (not held yet)"
                return fit, f"{e.path} is your build, {held} held, and this feeds {feeds}"
            return fit, f"{e.path} is your committed Path ({held} held)"

    # Otherwise fall back on whether the team wants the effect at all — minus any
    # tag the Curio only carries because of what it does *to* you. Cosmic Big
    # Lotto is tagged `hp` for losing 99% of it, and reporting that as "fits the
    # team: hp" directly above "can cost 99% of current HP" is the kind of
    # confident contradiction a reader has no way to resolve.
    tags = [t for t in entry.get("tags", [])
            if not (t == "hp" and any(e.kind == "hp_risk" for e in effects))]
    syn, matched = synergy_score(tags, team_tags(run),
                                 scoring.weights()["generic_good_tags"])
    if matched:
        return min(1.0, 0.35 + syn), "fits the team: " + ", ".join(matched[:3])
    return 0.30, "nothing your team specifically wants, so this is generic value"


def _downside(effects: list[Effect], run: RunState) -> tuple[float, list[str]]:
    """Stated costs, as a positive number to subtract."""
    total, reasons = 0.0, []
    for e in effects:
        if e.kind not in DOWNSIDE:
            continue
        base = DOWNSIDE[e.kind] * e.magnitude
        if e.kind == "fragment_wipe":
            # The only downside on this shelf that gets worse the better your run
            # is going. Flat-rating it would call the same card equally bad at 80
            # fragments and at 1,292, and at 1,292 it is the most dangerous thing
            # in the store.
            at_risk = run.fragments
            base *= min(1.6, 0.4 + at_risk / 900)
            reasons.append(
                f"it takes all {economy._n(at_risk)} of your fragments first and hands back "
                f"10%-200% of them. That is roughly break-even on average and a coin flip "
                f"on your whole balance, which is not the same thing")
        else:
            reasons.append(e.note)
        total += base
    return total, reasons


def _blessing_value(entry: dict, run: RunState) -> dict:
    """What a Blessing on the shelf is worth, from the ordinary blessing scorer.

    Nothing is re-derived here. `score_entry` already weighs Equation progress
    at 40 points and penalises spreading into an uncommitted Path, which is
    exactly the judgement the Blessing Store screen wants — its own top bar is a
    row of Equation progress counters. All this does is put that score on the
    same 0..1 scale as the Curio reader so one floor governs both shelves.
    """
    scored = scoring.score_entry(entry, run)
    # "advances no reachable equation" scores exactly zero, and it is the single
    # most important thing to say about a Legendary that has stopped being worth
    # buying — filtering it out for scoring nothing left a card explaining itself
    # as "Legendary, immediate power", which reads like a recommendation.
    always = {"equation progress", "path focus"}
    reasons = [f.note for f in scored.factors
               if f.note and (abs(f.points) > 0.05 or f.name in always)]
    # No driver line is added on top: the equation-progress factor's own note
    # already says "completes X" or "1 more toward X", and phrasing the same
    # fact twice reads like two reasons rather than one.
    if run.is_enhanced(entry["id"]):
        reasons.append("you already hold this one enhanced")
    reasons = list(dict.fromkeys(reasons))
    return {
        "value": round(scored.total / BLESSING_SCALE, 3),
        "effects": [],
        "reasons": reasons,
        "readable": True,
        "points": round(scored.total, 1),
    }


def value(entry: dict, run: RunState) -> dict:
    """What this card is worth to this run, 0..~1.3, with the working shown."""
    if entry.get("kind") == "blessing":
        return _blessing_value(entry, run)
    effects = classify(entry)
    reasons: list[str] = []
    left = run.domains_left()
    deck_scale = min(1.0, left / DECK_HORIZON)

    gross = 0.0
    for e in effects:
        if e.kind not in PAYLOAD:
            continue
        base, when = PAYLOAD[e.kind]
        v = base * e.magnitude
        if when == "deck":
            v *= deck_scale
            reasons.append(
                f"{e.note}, which pays out over Domains you have yet to enter, and "
                f"{left} of {run.domain_total} are left"
                + (", so most of its value is already gone" if deck_scale < 0.6 else ""))
        else:
            reasons.append(e.note)
        gross += v

    fit, fit_why = _build_fit(effects, entry, run)
    reasons.append(fit_why)

    use_factor, use_why = uses(entry)
    if use_why:
        reasons.append(use_why)
    rand_factor, rand_why = randomness(entry)
    if rand_why:
        reasons.append(rand_why)
    gate_factor, gate_why = _gating(entry)
    if gate_why:
        reasons.append(gate_why)

    net = gross * (0.45 + 0.55 * fit) * use_factor * rand_factor * gate_factor

    down, down_why = _downside(effects, run)
    reasons.extend(down_why)
    net -= down

    if entry.get("is_negative"):
        net = min(net, -0.5)
        reasons.append("this is a downside Curio")

    readable = bool([e for e in effects if e.kind in PAYLOAD or e.kind in DOWNSIDE])
    if not readable:
        reasons.append(
            "the engine could not read what this Curio does from its text. Treat this "
            "score as no information and read the card yourself")

    return {
        "value": round(net, 3),
        "effects": [e.to_dict() for e in effects],
        "reasons": reasons,
        "readable": readable,
    }


# --------------------------------------------------------------------- deciding

def buy_floor(run: RunState) -> float:
    """The usefulness a purchase must show before it can be recommended.

    Deliberately not a function of your balance. Being able to afford something
    is not an argument for buying it, and a store screen is built to suggest
    otherwise. It does drop at the end of the run, because fragments you never
    spend are destroyed and at that point a marginal Curio really does beat a
    number on a counter.
    """
    return FLOOR * (ENDGAME_FLOOR_SCALE if run.endgame() else 1.0)


def _expected_shelf(run: RunState, size: int, rarities: list[str],
                    exclude: set[int], kind: str = "curio") -> tuple[float, int]:
    """Expected best value of a freshly stocked shelf, as an exact order statistic.

    Same method as `miracles._expected_best` and `waypoint._expected_draw`, and
    for the same reason: the refresh estimate must not wander between clicks.
    Biased slightly optimistic, since it draws from the whole pool and the store
    may not stock all of it.
    """
    collection = "blessings" if kind == "blessing" else "curios"
    pool = [c for c in dataset.load()[collection]
            if not c.get("is_negative") and c["id"] not in exclude]
    wanted = set(r for r in rarities if r)
    if wanted:
        matching = [c for c in pool if c.get("rarity") in wanted]
        if len(matching) >= max(1, size):
            pool = matching
    if not pool:
        return 0.0, 0
    scores = sorted(value(c, run)["value"] for c in pool)
    m = len(scores)
    n = max(1, size)
    expected = sum(v * (((i + 1) / m) ** n - (i / m) ** n) for i, v in enumerate(scores))
    return expected, m


@dataclass
class Shelf:
    """One store screen: the goods, their prices, and whether it can be refreshed."""
    items: list[dict] = field(default_factory=list)   # {"id": int, "cost": int}
    refresh_cost: int = 0
    refreshes_left: int | None = None
    kind: str = "curio"                               # "curio" | "blessing"


def _owned_field(kind: str) -> str:
    return "owned_blessings" if kind == "blessing" else "owned_curios"


def plan_shelf(candidates: list[dict], run: RunState, kind: str, floor: float) -> dict:
    """The best affordable *set* — what Batch Select is actually asking.

    `economy.plan_workbench` solves its version of this as an exact knapsack,
    because enhancing one Blessing does not change what enhancing another is
    worth. Here it does: three Remembrance cards bought together can complete an
    Equation that none of them completes alone, and an exact knapsack over
    independently-scored cards would systematically undervalue exactly the
    concentration this tool exists to encourage (design invariant 5).

    So the plan is built one card at a time, re-scoring what remains against the
    Path counts and the balance the previous pick left behind. That is greedy
    rather than optimal — but it is deterministic, so the plan still does not
    change when you reopen the tab, and it prices compounding correctly, which
    the optimal-but-blind version does not.
    """
    owned = _owned_field(kind)
    virtual = replace(run, **{owned: list(getattr(run, owned))})
    steps: list[dict] = []
    pool = list(candidates)
    stopped = ""

    while pool:
        best, best_net = None, 0.0
        priced_out = below_floor = unaffordable = 0
        for cand in pool:
            if cand["cost"] > virtual.fragments:
                unaffordable += 1
                continue
            v = value(cand["entry"], virtual)
            if v["value"] < floor:
                below_floor += 1
                continue
            penalty, _ = economy.fragment_cost_penalty(cand["cost"], virtual)
            net = v["value"] - penalty
            if net <= 0:
                priced_out += 1
            if net > best_net + 1e-9:
                best, best_net, best_value = cand, net, v["value"]
        if best is None:
            # Worth distinguishing: a shelf where nothing is good enough is a
            # different situation from one where the *next* card is good enough
            # but the balance has been spent down to where it no longer pays.
            if priced_out and steps:
                stopped = (
                    f"Stops here: {priced_out} card(s) left would still be useful, but "
                    f"with {economy._n(virtual.fragments)} fragments left they cost more "
                    f"of what remains than they give back.")
            elif not steps:
                # An empty plan is still a verdict and has to say why it is empty
                # (invariant 4). Silence here read as "the planner did not run".
                if below_floor:
                    stopped = (
                        f"No plan: all {below_floor} affordable card(s) score under the "
                        f"{floor:.2f} bar for spending fragments at all, so there is no "
                        f"combination worth buying, not even a cheap one.")
                elif unaffordable:
                    stopped = (
                        f"No plan: {economy._n(virtual.fragments)} fragments is not enough "
                        f"for anything on this shelf.")
                else:
                    stopped = (
                        f"No plan: every card costs more of the "
                        f"{economy._n(virtual.fragments)} fragments you hold than it gives "
                        f"back at this point in the run.")
            break
        steps.append({
            "id": best["entry"]["id"], "name": best["entry"]["name"],
            "rarity": best["entry"].get("rarity", ""),
            "path": best["entry"].get("path", ""),
            "cost": best["cost"], "value": round(best_value, 2),
            "net": round(best_net, 2),
        })
        getattr(virtual, owned).append(best["entry"]["id"])
        virtual.fragments -= best["cost"]
        pool = [c for c in pool if c["entry"]["id"] != best["entry"]["id"]]

    spend = sum(s["cost"] for s in steps)
    notes: list[str] = []
    if stopped:
        notes.append(stopped)
    if len(steps) > 1:
        paths = [s["path"] for s in steps if s["path"]]
        if paths and len(set(paths)) < len(paths):
            top = max(set(paths), key=paths.count)
            notes.append(
                f"{paths.count(top)} of these are {top}. They go together because each "
                f"one makes the next worth more, which a card-by-card ranking cannot see.")
        # Only worth saying where it is true: Curios carry no Path, so the set is
        # simply the affordable best rather than a compounding one.
        notes.append(
            "Built one card at a time, re-scoring after each: buying two cards on the "
            "same Path is worth more than the two scores added up."
            if paths else
            "Built one card at a time, re-scoring after each, so the second card is "
            "judged against the balance the first one left.")
    if steps and run.fragments - spend > 0:
        notes.append(
            f"That leaves {economy._n(run.fragments - spend)} fragments. Unlike Heat, "
            f"you do not waste them. They fund the {max(0, run.domains_left() - 1)} "
            f"Domain(s) after this one.")
    return {
        "steps": steps, "spend": spend,
        "leftover": max(0, run.fragments - spend),
        "notes": notes,
    }


def decide_store(shelf: Shelf, run: RunState) -> dict:
    """Rank a store shelf against the hard pass — which is the default answer.

    Returns the verdicts in `economy.Verdict` shape so the UI renders them the
    same way as every other spend surface, plus a headline that states the
    recommendation in one sentence, because a store is a screen you want to
    leave rather than study.
    """
    verdicts: list[economy.Verdict] = []
    scored: list[dict] = []
    held: list[str] = []
    candidates: list[dict] = []
    floor = buy_floor(run)
    owned_ids = set(getattr(run, _owned_field(shelf.kind)))

    for item in shelf.items:
        entry = dataset.get(shelf.kind, item["id"])
        if entry is None:
            continue
        # Already in the bag — the same treatment `decide_workbench` gives an
        # "Already Enhanced" Blessing, and for the same reason: a shelf you have
        # just bought from still lists the card, and recommending it again put
        # "Buy Sealing Wax of Erudition" directly above a button reading "Held —
        # in your curios". Dropped from the candidates and reported, rather than
        # merely penalised, because a penalty still lets it win a weak shelf.
        if entry["id"] in owned_ids:
            held.append(entry["name"])
            continue
        cost = int(item.get("cost") or 0)
        v = value(entry, run)
        reasons = list(v["reasons"])

        penalty, cost_why = economy.fragment_cost_penalty(cost, run)
        affordable = cost <= run.fragments
        if cost_why:
            reasons.append(cost_why)
        net = v["value"] - (penalty if affordable else 0.0)

        # Price is set by rarity, so the expensive card has to be proportionally
        # more useful — the same trap as the Workbench's 1/2/3 Heat.
        cheapest = min((int(i.get("cost") or 0) for i in shelf.items), default=0)
        if cheapest and cost > cheapest:
            reasons.append(
                f"at {economy._n(cost)} it costs {cost / cheapest:.1f}x the cheapest card "
                f"here, so it has to be that much more useful, not merely rarer")

        if not affordable:
            net = -99.0
        elif v["value"] < floor:
            reasons.insert(0, (
                f"below the bar for spending at all. This is worth {v['value']:.2f} to your "
                f"run and a purchase needs {floor:.2f}. You can afford it, and that is not a "
                f"reason to buy it."))

        verdicts.append(economy.Verdict(
            action="buy", target=entry["name"], score=net, reasons=reasons,
            cost=cost, currency="Cosmic Fragments", affordable=affordable,
            path=entry.get("path", ""), entry_id=entry["id"],
        ))
        scored.append({
            "id": entry["id"], "name": entry["name"], "rarity": entry.get("rarity", ""),
            "path": entry.get("path", ""),
            "desc": entry.get("desc", "") or entry.get("effect", ""), "cost": cost,
            "value": v["value"], "net": round(net, 3), "clears_floor": v["value"] >= floor,
            "effects": v["effects"], "readable": v["readable"],
        })
        if affordable:
            candidates.append({"entry": entry, "cost": cost})

    # --- the pass ----------------------------------------------------------
    passing = [s for s in scored if not s["clears_floor"] or s["net"] <= 0]
    best = max((s for s in scored if s["clears_floor"] and s["net"] > 0),
               key=lambda s: s["net"], default=None)

    skip_reasons = []
    if held:
        skip_reasons.append(
            f"{len(held)} card(s) here are already in your bag and are not candidates: "
            f"{', '.join(held)}. If the store really is offering a second copy, judge that "
            f"one yourself. The engine assumes it is the one you just bought.")
    if not scored and not held:
        skip_reasons.append("nothing entered from the shelf yet")
    elif not scored:
        skip_reasons.append("nothing left on this shelf that you do not already hold")
    elif best is None:
        skip_reasons.append(
            f"none of the {len(scored)} card(s) on this shelf clears the bar for spending "
            f"fragments on your run, so walk out")
        if run.fragments > 0 and not run.endgame():
            skip_reasons.append(
                f"your {economy._n(run.fragments)} fragments keep their full value for the "
                f"{run.domains_left() - 1} Domain(s) after this one, and the shelves ahead "
                f"are drawn from the same pool")
    else:
        skip_reasons.append(
            f"holding your fragments instead of buying {best['name']}, worth doing if you "
            f"would rather bank them for a Domain that offers something on your Path")
    if run.endgame() and run.fragments > 0:
        skip_reasons.append(
            f"{run.domains_left()} Domain(s) left, so the run is about to delete whatever "
            f"you have not spent. The bar for buying is lower than usual here, not higher")
    verdicts.append(economy.Verdict(
        action="skip", target="Buy nothing, walk out", score=0.0, reasons=skip_reasons))

    # --- the refresh -------------------------------------------------------
    if shelf.refresh_cost:
        current = max((s["value"] for s in scored), default=0.0)
        rarities = [e.get("rarity", "") for e in
                    (dataset.get(shelf.kind, i["id"]) for i in shelf.items) if e]
        expected, pool_size = _expected_shelf(
            run, max(1, len(shelf.items)), rarities,
            exclude=owned_ids | {s["id"] for s in scored}, kind=shelf.kind)
        gain = expected - current
        penalty, cost_why = economy.fragment_cost_penalty(shelf.refresh_cost, run)
        noun = "Blessings" if shelf.kind == "blessing" else "Curios"
        rreasons = [
            f"a fresh shelf of {max(1, len(shelf.items))} is worth about {expected:.2f} to "
            f"this run, drawn from the {pool_size} {noun} that could appear",
            f"what is in front of you is worth {current:.2f}, so refreshing is worth "
            f"{gain:+.2f} before its price",
        ]
        if cost_why:
            rreasons.append(cost_why)
        rreasons.append(
            "the estimate cannot see what the store actually stocks, so it runs optimistic, "
            "and you still have to pay for whatever the refresh turns up")
        if shelf.refreshes_left is not None:
            rreasons.append(f"{shelf.refreshes_left} refresh(es) left")
        verdicts.append(economy.Verdict(
            action="refresh", target="Refresh the shelf", score=gain - penalty,
            reasons=rreasons, cost=shelf.refresh_cost, currency="Cosmic Fragments",
            affordable=shelf.refresh_cost <= run.fragments,
        ))

    # A card below the floor sorts *under* the skip line however positive its
    # arithmetic is. Without this the floor was only a warning: a card worth 0.23
    # against a floor of 0.30 still scored +0.22 against skip's 0.00 and won the
    # recommendation while printing "that is not a reason to buy it" underneath.
    # The score shown stays the true net — only the ordering knows about the bar.
    blocked = {s["name"] for s in scored if not s["clears_floor"]}
    verdicts.sort(key=lambda v: (0 if v.target in blocked else 1, v.score), reverse=True)

    # --- what to actually do ------------------------------------------------
    top = verdicts[0] if verdicts else None
    skip_v = next(v for v in verdicts if v.action == "skip")
    headline = ""

    plan = plan_shelf(candidates, run, shelf.kind, floor)
    # One card is not a batch, and showing a one-step "plan" above an identical
    # ranked list is noise.
    if len(plan["steps"]) < 2:
        plan["steps"] = []

    walking_out = (top is None or top.action == "skip"
                   or top.score - skip_v.score < MARGIN)

    if walking_out:
        recommendation = "pass"
        skip_v.recommended = True
        if not scored and held:
            headline = (f"Nothing to weigh. You already hold "
                        f"{'both' if len(held) == 2 else 'all'} of these.")
        elif best is None and scored:
            headline = (
                f"Hard pass. Nothing on this shelf is worth Cosmic Fragments to a run "
                f"{run.domain_index}/{run.domain_total} in.")
        elif scored:
            headline = (
                f"Pass. {top.target if top else ''} is only {top.score:+.2f} against walking "
                f"out, inside the margin where holding the fragments wins.")
        else:
            headline = "Enter what the shelf is offering and its prices."
    else:
        recommendation = top.action
        top.recommended = True
        if top.action == "refresh":
            headline = "Refresh rather than buy: nothing here beats a fresh shelf."
        elif plan["steps"]:
            # Batch Select makes the *set* the real answer — leading with a
            # single card would be the same mistake as reading the Workbench's
            # ranked list top-down when the prices differ.
            headline = (
                f"Buy {len(plan['steps'])} of the {len(scored)} for "
                f"{economy._n(plan['spend'])}: {', '.join(s['name'] for s in plan['steps'])}. "
                f"The rest are a pass.")
        else:
            headline = (f"Buy {top.target} for {economy._n(top.cost)}, and only that one. "
                        f"Everything else here is a pass.")

    return {
        "verdicts": [v.to_dict() for v in verdicts],
        "items": sorted(scored, key=lambda s: -s["net"]),
        "plan": plan,
        "kind": shelf.kind,
        "recommendation": recommendation,
        "headline": headline,
        "floor": round(floor, 3),
        "passing": [s["name"] for s in passing],
        "held": held,
        "fragments": run.fragments,
        "fragment_scarcity": round(economy.fragment_scarcity(run), 2),
        "domains_left": run.domains_left(),
        "endgame_advice": economy.spend_everything_advice(run),
    }
