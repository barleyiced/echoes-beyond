"""Classify Occurrence, shop and Workbench dialogue options by what they do.

`RogueDialogueOptionDisplay` carries 2260 option texts, 1093 of which are
reachable from `RogueDialogueOption`. They read like:

    "Purchase a 1-star Blessing."  / "Consume #2 Cosmic Fragment(s)."
    "Pick Leo."                    / "Discard 1 random Curio and obtain #1 ..."
    "Turn around and walk away."   / "You're not interested."

The `#N` placeholders resolve at *runtime* — the ParamList on the option row
does not line up with them — so this module deliberately does not invent
numbers. It extracts the **semantics** (what an option gives, costs and risks);
the live figures come from whatever is on screen when you paste a screenshot.

Effects are booleans rather than magnitudes for the same reason: "this option
grants a Curio and discards one" is reliable, "this option grants 2.4 Curios" is
not.
"""

from __future__ import annotations

import re

# Ordered so that more specific patterns can override the generic ones below.
EFFECT_PATTERNS: dict[str, list[str]] = {
    # --- what you get ---
    "gain_blessing": [r"obtain[^.]*blessing", r"gain[^.]*blessing", r"receive[^.]*blessing"],
    "gain_curio": [r"obtain[^.]*curio", r"gain[^.]*curio", r"receive[^.]*curio"],
    # "Retrieve"/"harvest" is how an escalating gamble words *cashing out* — the
    # fragments come back to you, so it is a gain, and (below) also a stop.
    "gain_fragments": [r"obtain[^.]*cosmic fragment", r"gain[^.]*cosmic fragment",
                       r"retrieve[^.]*cosmic fragment", r"harvest[^.]*cosmic fragment"],
    "gain_equation": [r"obtain[^.]*equation", r"gain[^.]*equation"],
    "gain_weighted": [r"obtain[^.]*weighted curio"],
    "heal": [r"\bheal\b", r"restores? .*\bhp\b", r"recover[^.]*\bhp\b"],

    # --- what it costs ---
    # "Insert" is the lottery/slot-machine verb ("Insert 200 Cosmic Fragments").
    # It reads as flavour but it is a spend, and missing it left every gambling
    # Occurrence scoring as though it were free.
    "cost_fragments": [r"consumes?[^.]*cosmic fragment", r"spend[^.]*cosmic fragment",
                       r"pay[^.]*cosmic fragment", r"insert[^.]*cosmic fragment"],
    "cost_curio": [r"discard \d* ?\w* ?curio", r"consumes?[^.]*curio", r"lose[^.]*curio"],
    "cost_blessing": [r"discard[^.]*blessing", r"consumes?[^.]*blessing", r"lose[^.]*blessing"],
    "cost_hp": [r"lose[^.]*\bhp\b", r"consumes?[^.]*\bhp\b", r"at the cost of[^.]*\bhp\b"],
    "cost_heat": [r"consumes?[^.]*heat", r"spend[^.]*heat"],

    # --- what it does ---
    "enhance": [r"\benhance", r"upgrade[^.]*blessing", r"increase[^.]*rarity"],
    "reforge": [r"overwrite", r"reforge", r"recast", r"re-?roll", r"refresh"],
    "synthesize": [r"synthesi[sz]e", r"compose"],
    "remove_negative": [r"remove[^.]*(negative|curse)", r"dispel[^.]*curse"],
    "domain": [r"\bdomain\b", r"\bbeacon\b"],
    "combat": [r"enter combat", r"start[^.]*battle", r"fight"],

    # --- risk ---
    "gamble": [r"\bbet\b", r"gambl", r"wager", r"random(ly)? (choose|select)", r"\bdice\b",
               r"chance to", r"\bluck\b", r"coin", r"lotter", r"jackpot", r"raffle",
               r"slot machine", r"you lost everything"],
    "unknown_outcome": [r"\bmight\b", r"\bmay\b", r"unknown", r"mysterious", r"unpredictable"],

    # --- neutral ---
    # The stop action. Broader than "walk away" because an escalating Occurrence
    # words it as quitting while ahead, and that is the option the engine most
    # often needs to rank against carrying on.
    "leave": [r"^leave", r"walk away", r"not interested", r"do nothing", r"refuse", r"decline",
              r"^\s*(just )?give up", r"\bquit\b", r"won'?t fall for", r"restrain your",
              r"want to leave", r"no,? thanks", r"never mind", r"forget it",
              r"^ignore\b", r"\bi'?ll pass\b"],
}

_COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in EFFECT_PATTERNS.items()}

# Effects that make an option strictly a cost with no listed upside.
PURE_COST = {"cost_fragments", "cost_curio", "cost_blessing", "cost_hp", "cost_heat"}
GAINS = {"gain_blessing", "gain_curio", "gain_fragments", "gain_equation",
         "gain_weighted", "heal", "enhance", "remove_negative"}

# Where an option is likely to appear. Used to filter the catalog when you tell
# the app which screen you are on.
CONTEXT_PATTERNS = {
    "shop": [r"purchase", r"\bbuy\b", r"\bshop\b", r"\bstore\b", r"discount", r"\bprice\b"],
    "workbench": [r"\bheat\b", r"workbench", r"\benhance", r"overwrite", r"synthesi[sz]e"],
    "occurrence": [],   # default
}
_CTX = {k: [re.compile(p, re.I) for p in v] for k, v in CONTEXT_PATTERNS.items()}


def group_of(option_id: int) -> int:
    """Which set of options this one appears alongside.

    There is no event -> option column anywhere in the fetched tables, but the
    display ids are allocated in blocks of 100 and each block is one event's
    dialogue. Block 2253 is the whole of the Dolos Dice lottery — the opening
    insert, every "keep going" and every "cash out", and both endings — and 2254
    is the whole Stone Mirror event. 132 blocks against 118 handbook events, and
    every block inspected has been internally coherent.

    This is an inference from id layout, not a join, so it is used only to *offer*
    the sibling options rather than to assert anything about them. The blocks are
    deliberately not given event names: the handbook titles cannot be joined to
    them, and a guessed label would be worse than none.
    """
    return option_id // 100


def classify(title: str, desc: str) -> list[str]:
    """Effect tags for one option."""
    blob = f"{title} {desc}"
    return sorted(k for k, pats in _COMPILED.items() if any(p.search(blob) for p in pats))


def context_of(title: str, desc: str) -> str:
    blob = f"{title} {desc}"
    for ctx, pats in _CTX.items():
        if pats and any(p.search(blob) for p in pats):
            return ctx
    return "occurrence"


def is_pure_cost(effects: list[str]) -> bool:
    e = set(effects)
    return bool(e & PURE_COST) and not (e & GAINS)


def risk_level(effects: list[str]) -> str:
    e = set(effects)
    # Declining a gamble is not a gamble. Walk-away options often name the thing
    # they are refusing ("...lottery-type products"), which otherwise made the
    # safest option on the screen read as the riskiest.
    if "leave" in e:
        e -= {"gamble", "unknown_outcome"}
    if "gamble" in e or "unknown_outcome" in e:
        return "high"
    if e & {"cost_curio", "cost_blessing", "cost_hp"}:
        return "medium"
    if "cost_fragments" in e:
        return "low"
    return "none"


# Numbers that appear literally in the text (not #N placeholders) are safe to
# read. Anything templated must come from the screen instead.
LITERAL_NUM_RE = re.compile(r"(?<![#\w])(\d+)(?!\])")


def literal_numbers(text: str) -> list[int]:
    return [int(m) for m in LITERAL_NUM_RE.findall(text or "")]


def has_runtime_placeholder(text: str) -> bool:
    return bool(re.search(r"#\d+", text or ""))


def parse_observed_cost(text: str) -> int | None:
    """Pull a Cosmic Fragment cost out of text captured from the screen.

    OCR sees the resolved value ("Consume 50 Cosmic Fragments"), which is the
    only reliable source for these numbers.
    """
    m = re.search(r"(\d+)\s*cosmic fragment", text or "", re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:consume|spend|pay|cost)\w*\s+(\d+)", text or "", re.I)
    return int(m.group(1)) if m else None


def parse_observed_heat(text: str) -> tuple[int | None, int | None]:
    """Read a 'current / max' Heat display such as '5/10'."""
    m = re.search(r"(\d+)\s*/\s*(\d+)", text or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+)\s*heat", text or "", re.I)
    return (int(m.group(1)), None) if m else (None, None)
