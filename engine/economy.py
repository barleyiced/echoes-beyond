"""Should you spend, reroll, or walk away?

Every spend decision is scored against the **skip** baseline. Skip is a real
competitor here, not a fallback: if nothing on offer beats doing nothing, the
answer is do nothing, and the engine says so.

Two rules this module exists to enforce:

1. **Rarity never justifies a spend on its own.** Upgrading a Legendary your
   build cannot use is worth less than upgrading a Rare that sits on the
   Equation you are actually chasing. Value is `marginal gain x relevance`, and
   relevance comes from Equation tracks, team synergy and committed mechanics —
   not from the rarity label.

2. **Heat and Cosmic Fragments are not the same kind of currency.** Heat resets
   at every Workbench of Creation ("Heat will be reset for every Workbench"), so
   unspent Heat is simply wasted and its opportunity cost is near zero. Fragments
   persist, so spending them has a genuine cost that depends on how much run is
   left to spend them in. On the final Plane that cost collapses too — fragments
   you never spend are also wasted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine import dataset, equations
from engine.state import PLANES, RunState
from engine.synergy import synergy_score, team_tags

# Rough fragments one Domain's worth of purchases costs. Only used to judge
# whether your balance is scarce or comfortable, so a loose estimate is fine.
# Expressed per Domain rather than per Plane because the run length varies
# (13, 17 or 20 Domains depending on difficulty).
TYPICAL_SPEND_PER_DOMAIN = 28


def _n(value: int | float) -> str:
    """Thousands-separated, since fragment balances run into four figures."""
    return f"{int(value):,}"


@dataclass
class Verdict:
    action: str                 # "upgrade" | "refresh" | "buy" | "skip"
    target: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    cost: int = 0
    currency: str = ""
    recommended: bool = False
    # False when you simply do not have the currency. Kept separate from the
    # score so the UI can say "cannot afford" instead of printing the -99
    # sentinel that pushes it to the bottom of the ranking.
    affordable: bool = True
    # The Path of whatever is being acted on, so the UI can show its icon.
    # Blank for anything not Path-bound — curios, refresh, skip.
    path: str = ""
    # The dataset id behind `target`, so the UI can act on the thing itself
    # rather than matching by name. 0 for skip and refresh, which name nothing.
    entry_id: int = 0

    def to_dict(self) -> dict:
        return {
            "action": self.action, "target": self.target,
            "score": round(self.score, 2), "reasons": self.reasons,
            "cost": self.cost, "currency": self.currency,
            "recommended": self.recommended, "affordable": self.affordable,
            "path": self.path, "entry_id": self.entry_id,
        }


# --------------------------------------------------------------------- currency

def planes_left(run: RunState) -> int:
    return max(0, PLANES - run.plane + 1)


def fragment_scarcity(run: RunState) -> float:
    """How precious one Cosmic Fragment is right now, ~0 (free) .. ~1.5 (tight).

    Driven by Domains remaining rather than Planes, because that is the number
    the game actually shows you. Fragments do not survive the run, so as the
    counter approaches its total the value of an unspent fragment collapses
    toward zero — at 15/17 hoarding is simply throwing the balance away.
    """
    left = run.domains_left() - 1          # the Domain you are in is the decision at hand
    if left <= 0:
        return 0.05
    expected_need = left * TYPICAL_SPEND_PER_DOMAIN
    if run.fragments <= 0:
        return 1.5
    scarcity = expected_need / max(1, run.fragments)
    # Taper hard over the last few Domains regardless of balance.
    if left <= 3:
        scarcity *= left / 4.0
    return max(0.05, min(1.5, scarcity))


def fragment_cost_penalty(cost: int, run: RunState) -> tuple[float, str]:
    """Normalised pain of spending `cost` fragments, plus an explanation."""
    if cost <= 0:
        return 0.0, ""
    if cost > run.fragments:
        return 99.0, f"you only have {_n(run.fragments)} fragments"
    scarcity = fragment_scarcity(run)
    share = cost / max(1, run.fragments)
    penalty = share * scarcity
    left = run.domains_left() - 1
    if scarcity <= 0.25:
        why = (f"{_n(cost)} fragments, and only {left} Domain(s) left after this one. "
               f"You lose whatever you have not spent when the run ends")
    elif share > 0.5:
        why = (f"{_n(cost)} fragments is {int(share * 100)}% of your balance, "
               f"with {left} Domain(s) still to fund")
    else:
        why = f"{_n(cost)} of {_n(run.fragments)} fragments"
    return penalty, why


def spend_everything_advice(run: RunState) -> str | None:
    """A blunt reminder when the run is nearly over and currency is still banked."""
    if not run.endgame():
        return None
    bits = []
    if run.fragments > 0:
        bits.append(f"{_n(run.fragments)} Cosmic Fragments")
    if run.heat > 0:
        bits.append(f"{run.heat} Heat")
    if not bits:
        return None
    left = run.domains_left()
    return (f"Domain {run.domain_index}/{run.domain_total}, {left} left. You are still holding "
            f"{' and '.join(bits)}. None of it carries over, so spend it on anything that "
            f"helps at all rather than banking it.")


def heat_cost_penalty(cost: int, run: RunState) -> tuple[float, str]:
    """Heat resets at every Workbench, so spending it costs almost nothing."""
    if cost <= 0:
        return 0.0, ""
    if cost > run.heat:
        return 99.0, f"you only have {run.heat} Heat"
    leftover = run.heat - cost
    why = f"{cost} of {run.heat} Heat"
    if leftover > 0:
        why += (f", so {leftover} would still be left over. You lose any Heat you "
                f"still hold when you leave this Workbench")
    return 0.05 * (cost / max(1, run.heat)), why


# -------------------------------------------------------------------- relevance

def relevance(entry: dict, run: RunState) -> tuple[float, list[str]]:
    """How much this specific blessing matters to *this* build, 0..1.

    This is the guard against upgrading something merely because it is rare.
    """
    reasons: list[str] = []
    score = 0.0

    path = entry.get("path")
    counts = run.path_counts()

    # On an Equation you are actually chasing?
    if path:
        tracked = [s for s in equations.reachable(counts, run.picks_remaining())
                   if any(r["path"] == path for r in s.equation["requires"])]
        active = [s for s in tracked if s.active]
        if active:
            best = max(active, key=lambda s: s.value)
            score += 0.45 * best.value
            reasons.append(f"{path} already powers {best.equation['name']}")
        elif tracked:
            best = min(tracked, key=lambda s: s.distance)
            closeness = 1.0 - min(1.0, best.distance / max(1, best.equation["total_required"]))
            score += 0.35 * best.value * closeness
            reasons.append(f"{path} is {best.distance} from {best.equation['name']}")
        else:
            reasons.append(f"{path} is not on any Equation you can still finish")

        held = counts.get(path, 0)
        if held >= 3:
            score += 0.15
            reasons.append(f"you hold {held} {path} blessings")

    # Does the team actually want this effect?
    profile = team_tags(run)
    syn, matched = synergy_score(entry.get("tags", []), profile, [])
    if syn > 0:
        score += 0.40 * syn
        reasons.append("fits the build: " + ", ".join(matched[:3]))
    elif run.team:
        reasons.append("nothing the team specifically wants")

    return min(1.0, score), reasons


def enhance_value(entry: dict, run: RunState) -> tuple[float, list[str]]:
    """Value of enhancing one owned blessing = marginal gain x relevance.

    `upgrade_gain` is the real numeric delta between the blessing's levels, taken
    from the game's own ParamList — not a guess from its rarity.
    """
    gain = entry.get("upgrade_gain")
    rel, reasons = relevance(entry, run)

    if gain is None:
        # Some blessings gain a clause rather than a bigger number.
        gain_norm = 0.35
        reasons.insert(0, "no numeric scaling, so the upgrade adds an effect rather than a bigger number")
    else:
        gain_norm = min(1.0, gain)
        reasons.insert(0, f"+{int(gain * 100)}% on its own numbers at max level")

    value = gain_norm * rel
    if rel < 0.25:
        reasons.append("low relevance, so the upgrade mostly does nothing for this run")
    return value, reasons


# --------------------------------------------------------------------- decisions

def _refresh_value(offered: list[dict], run: RunState) -> tuple[float, list[str]]:
    """Expected gain from rerolling, versus keeping the best thing on offer.

    Rerolling is only worth it when what is in front of you is weak. If the
    current offer already advances your build, a reroll is a coin flip you are
    paying for.
    """
    from engine.scoring import score_entry

    if not offered:
        return 0.35, ["nothing on offer, so a reroll can only improve things"]

    best = max(score_entry(e, run).total for e in offered)
    # Typical blessing scores land in the 20-45 band; treat ~32 as an average draw.
    average_draw = 32.0
    if best >= average_draw:
        return 0.0, [f"the best option here already scores {best:.0f}, above a typical draw"]
    deficit = (average_draw - best) / average_draw
    return min(0.6, deficit), [f"best on offer only scores {best:.0f}, below a typical draw"]


def decide_workbench(run: RunState, candidate_ids: list[int] | None = None) -> list[Verdict]:
    """Rank what to do with the Heat at this Workbench of Creation.

    Heat does not carry over, so the bar for spending it is low — but *which*
    blessing to spend it on still matters, and a blessing your build cannot use
    is not worth Heat merely because it is Legendary.
    """
    ids = candidate_ids if candidate_ids is not None else run.owned_blessings
    verdicts: list[Verdict] = []
    already = 0

    for bid in ids:
        b = dataset.get("blessing", bid)
        if b is None:
            continue
        if b.get("max_level", 1) <= 1:
            continue
        # The game shows these as "Already Enhanced" and refuses the click; an
        # engine that keeps recommending them is recommending a no-op.
        if run.is_enhanced(bid):
            already += 1
            continue
        value, reasons = enhance_value(b, run)
        cost = run.enhance_cost(b)
        penalty, cost_why = heat_cost_penalty(cost, run)
        if cost_why:
            reasons.append(cost_why)
        # Rarity sets the price, so a Legendary has to be *three times* as useful
        # as a Common to be worth the same Heat. Ranking on value alone would
        # quietly recommend the expensive one every time.
        if cost > 1:
            reasons.append(
                f"{b.get('rarity', 'this')} rarity costs {cost} Heat, so it has to beat "
                f"{cost} cheaper enhances, not just one"
            )
        verdicts.append(Verdict(
            action="upgrade", target=b["name"], score=value - penalty,
            reasons=reasons, cost=cost, currency="Heat",
            affordable=cost <= run.heat, path=b.get("path", ""),
            entry_id=b["id"],
        ))

    verdicts.sort(key=lambda v: -v.score)

    skip_reasons = []
    affordable = [v for v in verdicts if v.cost <= run.heat]
    if run.heat <= 0:
        skip_reasons.append("no Heat left at this Workbench")
    elif affordable and affordable[0].score > 0.08:
        skip_reasons.append(
            "you lose any Heat you leave this Workbench with, so holding it back gains nothing"
        )
    elif verdicts and not affordable:
        # Distinct from "not worth it": since rarity sets the price, the whole
        # bench can be out of reach at low Heat, and saying the wrong one of
        # these sends you looking for a different Blessing instead of more Heat.
        cheapest = min(v.cost for v in verdicts)
        skip_reasons.append(
            f"you cannot afford any of them. The cheapest enhance you hold costs "
            f"{cheapest} Heat and you have {run.heat}"
        )
    elif already and not verdicts:
        skip_reasons.append(
            f"you have already enhanced every Blessing you hold that takes one "
            f"({already} of them), so nothing is left at this bench"
        )
    else:
        skip_reasons.append("nothing you hold is worth enhancing for this build")
    if already and verdicts:
        skip_reasons.append(f"{already} already enhanced and left out")
    skip = Verdict(action="skip", target="Spend nothing", score=0.08, reasons=skip_reasons)

    out = verdicts + [skip]
    out.sort(key=lambda v: -v.score)
    if out:
        out[0].recommended = True
    return out


def plan_workbench(run: RunState, candidate_ids: list[int] | None = None) -> dict:
    """The best *set* of enhances that fits the Heat in front of you.

    Ranking enhances one at a time was right while every one cost the same. They
    do not: a Common costs 1 Heat and a Legendary 3, so with 3 Heat the choice is
    one Legendary against three Commons, and the best single enhance is often the
    wrong first move. That is a knapsack, and at these sizes it can be solved
    exactly rather than approximated — an exact answer also means the plan does
    not change when you reopen the tab.

    Heat does not carry over, so leftovers are called out: unspent Heat is not
    saved, it is destroyed.
    """
    ids = candidate_ids if candidate_ids is not None else run.owned_blessings
    items: list[tuple[float, int, dict]] = []
    already = 0
    for bid in ids:
        b = dataset.get("blessing", bid)
        if b is None or b.get("max_level", 1) <= 1:
            continue
        if run.is_enhanced(bid):
            already += 1
            continue
        value, _ = enhance_value(b, run)
        if value <= 0:
            continue
        items.append((value, run.enhance_cost(b), b))

    budget = max(0, run.heat)
    # best[c] = (total value, indices) for exactly-at-most c Heat
    best: list[tuple[float, list[int]]] = [(0.0, []) for _ in range(budget + 1)]
    for i, (value, cost, _) in enumerate(items):
        if cost <= 0 or cost > budget:
            continue
        for c in range(budget, cost - 1, -1):
            cand_value = best[c - cost][0] + value
            if cand_value > best[c][0] + 1e-9:
                best[c] = (cand_value, best[c - cost][1] + [i])

    total_value, chosen = best[budget] if budget else (0.0, [])
    picked = [items[i] for i in chosen]
    picked.sort(key=lambda t: -t[0])
    spend = sum(c for _, c, _ in picked)
    leftover = budget - spend

    steps = [{
        "id": b["id"], "name": b["name"], "rarity": b.get("rarity", ""), "cost": c,
        "value": round(v, 2), "path": b.get("path", ""),
    } for v, c, b in picked]

    notes: list[str] = []
    if already:
        notes.append(f"You have already enhanced {already} Blessing(s) you hold, so they are "
                     f"not candidates. The game shows them as \"Already Enhanced\".")
    if not items:
        notes.append("Nothing you hold can be enhanced. Everything is already at max level.")
    elif not picked:
        notes.append(
            f"{budget} Heat is not enough for anything worth enhancing here."
            if budget else "No Heat at this Workbench.")
    if leftover > 0 and picked:
        cheapest = min((c for _, c, _ in items), default=0)
        if cheapest and leftover >= cheapest:
            notes.append(
                f"{leftover} Heat is left over, and something cheap can still use it. "
                f"Check the ranked list below.")
        else:
            notes.append(
                f"{leftover} Heat is left over with nothing cheap enough to spend it "
                f"on. You lose it when you leave, and nothing here can change that.")
    if picked and any(c > 1 for _, c, _ in picked):
        notes.append(
            "Costs differ by rarity, so this is the best combination that fits your Heat, "
            "not simply the highest-scoring enhances one after another.")

    return {
        "steps": steps,
        "spend": spend,
        "leftover": leftover,
        "heat": budget,
        "total_value": round(total_value, 2),
        "notes": notes,
    }


def decide_purchase(option: dict, run: RunState, observed_cost: int | None = None) -> Verdict:
    """Judge a single shop or event option against skip.

    `observed_cost` comes from the screenshot, because the cost in the game data
    is a runtime placeholder rather than a fixed number.
    """
    effects = set(option.get("effects", []))
    reasons: list[str] = []
    value = 0.0

    # What you get. These are deliberately coarse — the data gives semantics, not
    # magnitudes, and inventing magnitudes would be worse than admitting this.
    if "gain_equation" in effects:
        value += 0.55
        reasons.append("grants an Equation")
    if "gain_blessing" in effects:
        value += 0.35
        reasons.append("grants a Blessing")
    if "gain_weighted" in effects:
        value += 0.35
        reasons.append("grants a Weighted Curio")
    if "gain_curio" in effects:
        value += 0.25
        reasons.append("grants a Curio")
    if "enhance" in effects:
        value += 0.30
        reasons.append("enhances what you already hold")
    if "remove_negative" in effects:
        value += 0.30
        reasons.append("clears a negative effect")
    if "gain_fragments" in effects:
        value += 0.20 * fragment_scarcity(run)
        reasons.append("gains fragments" + (", and you are short" if run.fragments < 100 else ""))
    if "heal" in effects:
        value += 0.15
        reasons.append("restores HP")

    # What it costs you beyond currency.
    if "cost_curio" in effects:
        value -= 0.25
        reasons.append("costs you a Curio")
    if "cost_blessing" in effects:
        value -= 0.35
        reasons.append("costs you a Blessing")
    if "cost_hp" in effects:
        value -= 0.15
        reasons.append("costs HP")

    risk = option.get("risk", "none")
    if risk == "high":
        value *= 0.6
        reasons.append("the outcome is random. This is a gamble, and the engine cannot see the odds")
    elif risk == "medium":
        value *= 0.85

    # A line can look like an ordinary purchase while the Occurrence around it is
    # a slot machine — "Insert 200 Cosmic Fragments" says nothing about odds, and
    # only its sibling options mention the lottery. See build_options.
    if option.get("group_gamble") and risk != "high" and "leave" not in effects:
        value *= 0.7
        reasons.append(
            "this is part of a gambling Occurrence. The payout is random and the "
            "engine cannot see the odds, so treat any figure here as optimistic"
        )

    # An option whose text the classifier could not read must say so rather than
    # returning a bare 0.00 with no reasoning. A silent nothing looks like a
    # considered verdict and is the one thing this tool must not do.
    if not effects:
        reasons.append(
            "the engine could not read what this option does from its text. "
            "Treat this score as no information and judge it yourself"
        )

    cost = observed_cost or 0
    currency = ""
    if cost:
        currency = "Heat" if "cost_heat" in effects else "Cosmic Fragments"
        penalty, why = (heat_cost_penalty(cost, run) if currency == "Heat"
                        else fragment_cost_penalty(cost, run))
        value -= penalty
        if why:
            reasons.append(why)
    elif option.get("templated"):
        reasons.append("the game shows the cost on screen, not in the data. Scan or type it "
                       "for a firm answer")

    action = "skip" if "leave" in effects else "buy"
    if action == "skip" and not reasons:
        reasons.append(
            "walks away. Costs nothing, gains nothing, which is the bar every "
            "other option here has to clear"
        )
    return Verdict(action=action, target=option.get("name", ""), score=value,
                   reasons=reasons, cost=cost, currency=currency)


def decide_offer(options: list[dict], run: RunState, costs: dict[int, int] | None = None,
                 refresh_cost: int = 0, offered_entries: list[dict] | None = None) -> list[Verdict]:
    """Rank every option on an event or shop screen, including reroll and skip."""
    costs = costs or {}
    verdicts = [decide_purchase(o, run, costs.get(o["id"])) for o in options]

    if refresh_cost or offered_entries:
        rv, rreasons = _refresh_value(offered_entries or [], run)
        penalty, why = fragment_cost_penalty(refresh_cost, run)
        if why:
            rreasons.append(why)
        verdicts.append(Verdict(action="refresh", target="Reroll the offer",
                                score=rv - penalty, reasons=rreasons,
                                cost=refresh_cost, currency="Cosmic Fragments"))

    if not any(v.action == "skip" for v in verdicts):
        scarcity = fragment_scarcity(run)
        left = run.domains_left() - 1
        if scarcity > 0.8:
            why = f"fragments are tight ({_n(run.fragments)} left, {left} Domain(s) still to fund)"
        elif scarcity <= 0.2:
            why = (f"only {left} Domain(s) left. You lose whatever you do not spend, "
                   "so a marginal buy still beats saving")
        else:
            why = "keeping your fragments for a better offer"
        verdicts.append(Verdict(action="skip", target="Take nothing", score=0.05, reasons=[why]))

    verdicts.sort(key=lambda v: -v.score)
    if verdicts:
        verdicts[0].recommended = True
    return verdicts
