"""Derive mechanic tags from blessing / curio / equation description text.

The scoring engine cannot reason about prose, so at build time every entry is
reduced to a set of tags ("break", "dot", "follow_up", "sp_positive", ...). A
team is tagged the same way, and synergy becomes set overlap.

Tags are deliberately coarse. A blessing that says "increases Break Effect" and
a character that lives on Break DMG both carry `break`, and that is enough for a
heuristic. Precision beyond this would need a damage model we explicitly do not
have.
"""

from __future__ import annotations

import re

# Each tag maps to patterns matched case-insensitively against the rendered
# description. Order does not matter; an entry can carry any number of tags.
TAG_PATTERNS: dict[str, list[str]] = {
    # --- damage mechanics -------------------------------------------------
    "break": [r"\bbreak effect\b", r"\bweakness break\b", r"\btoughness\b", r"\bsuper break\b", r"\bbreak dmg\b"],
    "dot": [r"\bdot\b", r"\bdamage over time\b", r"\bburn\b", r"\bshock\b", r"\bwind shear\b",
            r"\bbleed\b", r"\berosion\b", r"\bentanglement\b"],
    "follow_up": [r"\bfollow-up attack\b", r"\bfollow up attack\b"],
    "ultimate": [r"\bultimate\b"],
    "skill": [r"\buses? (their )?skill\b", r"\bskill dmg\b"],
    "basic_atk": [r"\bbasic atk\b", r"\bbasic attack\b"],
    "summon": [r"\bmemosprites?\b", r"\bsummons?\b", r"\bservants?\b"],
    "aoe": [r"\ball enemies\b", r"\badjacent\b", r"\bblast\b", r"\bbounce\b"],
    "additional_dmg": [r"\badditional dmg\b", r"\baftertaste\b", r"\bquake\b", r"\berudition dmg\b"],

    # --- stats ------------------------------------------------------------
    "crit": [r"\bcrit rate\b", r"\bcrit dmg\b", r"\bcritical\b"],
    "atk": [r"\batk\b"],
    "spd": [r"\bspd\b", r"\bspeed\b"],
    "def_stat": [r"\bdef\b"],
    "hp": [r"\bmax hp\b", r"\bhp\b"],
    "effect_hit": [r"\beffect hit rate\b"],
    "effect_res": [r"\beffect res\b"],
    "res_pen": [r"\bres pen\b", r"\bresistance penetration\b", r"\bdef ignore\b", r"\bignores?\b.*\bdef\b"],
    "dmg_bonus": [r"\bdmg (increases?|boost|bonus)\b", r"\bincreases? .*dmg\b", r"\bdmg dealt\b"],
    "vulnerability": [r"\bvulnerabilit\w+\b", r"\breceives? increased dmg\b", r"\bdmg taken\b"],

    # --- economy / sustain ------------------------------------------------
    "sp_positive": [r"\brecovers? \d* ?skill point", r"\bregenerates? .*skill point", r"\bskill point"],
    "energy": [r"\benergy\b", r"\bregenerates? energy\b"],
    "heal": [r"\bheal\b", r"\brestores? hp\b", r"\boutgoing healing\b"],
    "shield": [r"\bshields?\b", r"\bbarriers?\b"],
    "cleanse": [r"\bdispel\b", r"\bcleanse\b", r"\bremoves? .*debuff\b"],
    "revive": [r"\brevive\b", r"\bresurrect\b"],

    # --- turn economy -----------------------------------------------------
    "action_forward": [r"\badvance\w* forward\b", r"\bextra turn\b", r"\baction advance\b"],
    "delay": [r"\bdelay\b", r"\bpush\w* back\b"],

    # --- debuffs / control ------------------------------------------------
    "debuff": [r"\bdebuffs?\b", r"\breduces? .*(atk|def|spd)\b"],
    "control": [r"\bfreeze\b", r"\bimprison\b", r"\bentangle\b", r"\bdominat\w+\b", r"\bconfine\b"],

    # --- defensive --------------------------------------------------------
    "mitigation": [r"\bdmg reduction\b", r"\breduces? dmg taken\b", r"\bdamage mitigation\b"],

    # --- run economy (mostly curios) --------------------------------------
    # Plurals matter here more than anywhere else: the game writes "Cosmic
    # Fragments" 43 times for every singular, so `\bcosmic fragment\b` matched
    # almost nothing and 24 entries — Sacrificial Javelin among them, a curio
    # whose entire effect is fragment income — carried no economy tag at all and
    # were ranked on base power alone with a "little machine-readable effect
    # text" warning attached.
    "economy": [r"\bcosmic fragments?\b", r"\bcurios?\b", r"\bblessings?\b",
                r"\bshops?\b", r"\bstores?\b", r"\bdiscounts?\b"],
    "reroll": [r"\breroll\b", r"\brefresh\b", r"\bre-?draw\b"],
    "upgrade": [r"\bupgrade\b", r"\benhance\b", r"\bworkbench\b"],
}

# Elements are matched separately so they can gate weighted curios exactly.
ELEMENT_PATTERNS = {
    "Physical": r"\bphysical\b",
    "Fire": r"\bfire\b",
    "Ice": r"\bice\b",
    "Thunder": r"\blightning\b|\bthunder\b",
    "Wind": r"\bwind\b",
    "Quantum": r"\bquantum\b",
    "Imaginary": r"\bimaginary\b",
}

_COMPILED = {tag: [re.compile(p, re.I) for p in pats] for tag, pats in TAG_PATTERNS.items()}
_COMPILED_ELEM = {el: re.compile(p, re.I) for el, p in ELEMENT_PATTERNS.items()}


QUOTED_RE = re.compile(r"\"([A-Z][A-Za-z' -]{2,28})\"")


def mechanic_terms(*texts: str) -> list[str]:
    """Quoted mechanic names referenced by the text, e.g. Obsession, Blazar.

    Each Path in a DU theme has exactly one signature mechanic (the game's own
    RogueTournKeyword table carries one per Path per theme), and blessings refer
    to it by name in quotes. Pulling these out of the text keeps the tagger
    working when a new theme invents new mechanics, instead of needing the
    pattern list updated every patch.
    """
    blob = " ".join(t for t in texts if t)
    terms = set()
    for m in QUOTED_RE.findall(blob):
        # Strip only a possessive suffix — rstrip("'s") would also eat the final
        # 's' of names like "Soul Chrysalis".
        term = re.sub(r"'s$", "", m).strip()
        if term:
            terms.add("mech:" + re.sub(r"[^a-z0-9]+", "_", term.lower()).strip("_"))
    return sorted(terms)


def tag_text(*texts: str) -> list[str]:
    """Return the sorted mechanic tags present across all supplied strings."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return []
    found = {tag for tag, pats in _COMPILED.items() if any(p.search(blob) for p in pats)}
    return sorted(found)


def elements_in(*texts: str) -> list[str]:
    blob = " ".join(t for t in texts if t)
    if not blob:
        return []
    return sorted(el for el, p in _COMPILED_ELEM.items() if p.search(blob))


# Path name -> the tags a team running that path naturally wants. Used to give a
# team a baseline tag set even before per-character detail is filled in.
PATH_AFFINITY: dict[str, list[str]] = {
    "Preservation": ["shield", "mitigation", "def_stat"],
    "Remembrance": ["summon", "crit", "dmg_bonus"],
    "Nihility": ["dot", "debuff", "vulnerability"],
    "Abundance": ["heal", "hp", "cleanse"],
    "The Hunt": ["crit", "follow_up", "spd"],
    "Destruction": ["atk", "crit", "hp"],
    "Elation": ["follow_up", "additional_dmg", "crit"],
    "Propagation": ["aoe", "atk", "additional_dmg"],
    "Erudition": ["aoe", "additional_dmg", "crit"],
    "Harmony": ["spd", "atk", "sp_positive", "energy"],
}
