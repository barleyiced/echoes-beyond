"""Identify fields in obfuscated game tables by the *shape of their values*.

Older tables (RogueTournBuff, RogueTournFormula, ...) ship readable keys.
Newer ones do not — RoguePersonaStyle arrives as::

    {"KLOEJIMMPJM": 101,
     "BCGJNNDCIFH": "SpriteOutput/Rogue/Tourn/Persona/.../Big_6.png",
     "MJOOFPBABEA": {"Hash": 13548995702470921752},
     "PBLPLDJKPEI": [{"Value": 45}, {"Value": 3}],
     ...}

Those keys are stable within one dump but reshuffle on every patch, so anything
that hardcodes them silently rots. We instead classify each *column* by what its
values look like across the whole table, then resolve the text columns through
TextMap to tell a name from a flavour blurb from an effect description.

`detect_roles()` is the entry point. `tests/test_shapes.py` pins its output
against the current dump — that test is the tripwire for the next reshuffle.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from enum import Enum
from typing import Any, Iterable

# Two placeholder syntaxes appear in game text: positional (#1[i]) and
# blackboard lookups (#{blackboard:MazeBuffParam_1}[i]). Both mark a string as
# an effect description rather than flavour.
PLACEHOLDER_RE = re.compile(r"#\d+\[[if]\d*%?\]|#\{blackboard:[^}]+\}")
MARKUP_RE = re.compile(r"<(color|unbreak|i|u|/)")
ICON_RE = re.compile(r"^(SpriteOutput|UI)/.*\.(png|jpg)$", re.I)
CONFIG_RE = re.compile(r"^Config/.*\.json$", re.I)

# Rarity/category enums used across the rogue tables. A string column whose
# values all fall in here is a category, not free text.
RARITY_WORDS = {
    "Common", "Rare", "Epic", "Legendary", "PathEcho",
    "Tourn1", "Tourn2", "Tourn3",
    "Active", "Attach", "Passive",
}


class Role(str, Enum):
    ID = "id"                     # unique integer primary key
    ORDER = "order"               # unique integer that is not the primary key (sort index)
    GROUP = "group"               # non-unique integer grouping key (path id, style id, ...)
    LEVEL = "level"               # small non-unique integer (1..5)
    NAME = "name"                 # short display text
    TAGLINE = "tagline"           # short coloured summary line
    FLAVOUR = "flavour"           # long prose with no numeric placeholders
    EFFECT = "effect"             # description containing #N[i] placeholders
    EFFECT_ALT = "effect_alt"     # a second placeholder-bearing description
    PARAMS = "params"             # [{"Value": n}, ...]
    COST = "cost"                 # [{"ItemID": .., "ItemNum": ..}]
    REFS = "refs"                 # list of integer ids
    ICON = "icon"                 # SpriteOutput/... path
    CONFIG = "config"             # Config/....json path
    CATEGORY = "category"         # enum-like string
    FLAG = "flag"                 # bool
    UNKNOWN = "unknown"


class ShapeError(RuntimeError):
    """Raised when a table does not contain the fields we require."""


# --------------------------------------------------------------------------
# value-level predicates
# --------------------------------------------------------------------------

def is_text_hash(v: Any) -> bool:
    return isinstance(v, dict) and set(v.keys()) <= {"Hash"} and "Hash" in v


def is_param_list(v: Any) -> bool:
    return (
        isinstance(v, list)
        and len(v) > 0
        and all(isinstance(x, dict) and set(x.keys()) == {"Value"} for x in v)
    )


def is_cost_list(v: Any) -> bool:
    return (
        isinstance(v, list)
        and len(v) > 0
        and all(isinstance(x, dict) and {"ItemID", "ItemNum"} <= set(x.keys()) for x in v)
    )


def is_id_list(v: Any) -> bool:
    return isinstance(v, list) and len(v) > 0 and all(isinstance(x, int) for x in v)


def is_icon(v: Any) -> bool:
    return isinstance(v, str) and bool(ICON_RE.match(v))


def is_config(v: Any) -> bool:
    return isinstance(v, str) and bool(CONFIG_RE.match(v))


# --------------------------------------------------------------------------
# column-level classification
# --------------------------------------------------------------------------

def _columns(rows: list[dict]) -> dict[str, list[Any]]:
    """Collect present (non-null) values per key. Keys absent from some rows are normal."""
    cols: dict[str, list[Any]] = {}
    for r in rows:
        for k, v in r.items():
            if v is None or v == "" or v == []:
                continue
            cols.setdefault(k, []).append(v)
    return cols


def _classify_structural(values: list[Any], n_rows: int) -> Role:
    """Classify a column from value shape alone, ignoring text semantics."""
    if all(isinstance(v, bool) for v in values):
        return Role.FLAG
    if all(is_text_hash(v) for v in values):
        return Role.NAME  # provisional; refined by _classify_text_columns
    if all(is_param_list(v) for v in values):
        return Role.PARAMS
    if all(is_cost_list(v) for v in values):
        return Role.COST
    if all(is_id_list(v) for v in values):
        return Role.REFS
    if all(is_icon(v) for v in values):
        return Role.ICON
    if all(is_config(v) for v in values):
        return Role.CONFIG
    if all(isinstance(v, str) for v in values):
        if set(values) <= RARITY_WORDS:
            return Role.CATEGORY
        # a small closed set of repeated strings is still an enum
        if len(set(values)) <= max(6, n_rows // 20):
            return Role.CATEGORY
        return Role.UNKNOWN
    if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        uniq = len(set(values))
        if uniq == len(values) == n_rows:
            return Role.ID
        if max(values) <= 10:
            return Role.LEVEL
        return Role.GROUP
    return Role.UNKNOWN


def _text_profile(values: list[Any], textmap: dict) -> dict:
    """Resolve a hash column through TextMap and describe what the strings look like."""
    resolved = [textmap.get(str(v["Hash"]), "") for v in values]
    resolved = [s for s in resolved if s]
    if not resolved:
        return {"n": 0}
    return {
        "n": len(resolved),
        "median_len": statistics.median(len(s) for s in resolved),
        "placeholder_rate": sum(bool(PLACEHOLDER_RE.search(s)) for s in resolved) / len(resolved),
        "markup_rate": sum(bool(MARKUP_RE.search(s)) for s in resolved) / len(resolved),
        "colour_start_rate": sum(s.startswith("<color") for s in resolved) / len(resolved),
        "samples": resolved[:3],
    }


def _classify_text_columns(
    text_cols: dict[str, list[Any]], textmap: dict
) -> dict[str, Role]:
    """Split hash columns into name / tagline / flavour / effect.

    Ordering rules, applied to the whole column rather than any single row:
      - placeholders (#1[i]) mean it is an effect description
      - a leading <color=...> wrapper marks the one-line summary
      - of what remains, the shortest is the name and longer ones are flavour
    """
    profiles = {k: _text_profile(v, textmap) for k, v in text_cols.items()}
    roles: dict[str, Role] = {}

    effects = [k for k, p in profiles.items() if p.get("placeholder_rate", 0) >= 0.5]
    effects.sort(key=lambda k: profiles[k]["median_len"], reverse=True)
    for i, k in enumerate(effects):
        roles[k] = Role.EFFECT if i == 0 else Role.EFFECT_ALT

    rest = [k for k in text_cols if k not in roles]
    taglines = [k for k in rest if profiles[k].get("colour_start_rate", 0) >= 0.5]
    for k in taglines:
        roles[k] = Role.TAGLINE

    rest = [k for k in rest if k not in roles]
    rest.sort(key=lambda k: profiles[k]["median_len"])
    for i, k in enumerate(rest):
        roles[k] = Role.NAME if i == 0 else Role.FLAVOUR

    return roles


def detect_roles(rows: list[dict], textmap: dict | None = None) -> dict[Role, str]:
    """Map each detected role to the (possibly obfuscated) key that carries it.

    Roles that can repeat (ICON, REFS, GROUP, ...) keep only the first key found;
    use `detect_all_roles` when you need every column.
    """
    all_roles = detect_all_roles(rows, textmap)
    out: dict[Role, str] = {}
    for key, role in all_roles.items():
        out.setdefault(role, key)
    return out


def detect_all_roles(rows: list[dict], textmap: dict | None = None) -> dict[str, Role]:
    """Classify every column. Returns {key: role} preserving table key order."""
    if not rows:
        raise ShapeError("empty table")

    cols = _columns(rows)
    n = len(rows)
    roles = {k: _classify_structural(v, n) for k, v in cols.items()}

    text_cols = {k: cols[k] for k, r in roles.items() if r is Role.NAME}
    if text_cols:
        if textmap is None:
            raise ShapeError("table has text-hash columns but no TextMap was supplied")
        roles.update(_classify_text_columns(text_cols, textmap))

    # Uniqueness alone does not identify the primary key: a sort-order column is
    # also unique (RoguePersonaStyle carries one). In every table we consume the
    # primary key is the *first* integer column, so keep that one and demote any
    # later unique-integer column to ORDER.
    seen_id = False
    for k, r in roles.items():
        if r is not Role.ID:
            continue
        if seen_id:
            roles[k] = Role.ORDER
        seen_id = True

    # Tables with a composite key (id, level) have no unique column at all, so
    # fall back to the first integer column.
    if not seen_id:
        for k, r in roles.items():
            if r in (Role.GROUP, Role.LEVEL):
                roles[k] = Role.ID
                break

    return roles


def require(roles: dict[Role, str], *needed: Role, table: str = "?") -> None:
    """Fail loudly when a table lost a field we depend on."""
    missing = [r.value for r in needed if r not in roles]
    if missing:
        raise ShapeError(
            f"{table}: could not locate required field(s) {missing}. "
            f"Detected roles: { {r.value: k for r, k in roles.items()} }. "
            "Upstream probably reshuffled its obfuscated keys — update data/shapes.py."
        )


def get(row: dict, roles: dict[Role, str], role: Role, default: Any = None) -> Any:
    """Read `role` out of `row` using the detected key mapping."""
    key = roles.get(role)
    if key is None:
        return default
    return row.get(key, default)


def text(row: dict, roles: dict[Role, str], role: Role, textmap: dict, default: str = "") -> str:
    v = get(row, roles, role)
    if not is_text_hash(v):
        return default
    return textmap.get(str(v["Hash"]), default)


def params(row: dict, roles: dict[Role, str]) -> list[float]:
    v = get(row, roles, Role.PARAMS) or []
    return [p.get("Value") for p in v if isinstance(p, dict)]


def describe(rows: list[dict], textmap: dict | None = None) -> str:
    """Human-readable dump of the detection result, for debugging a patch break."""
    out = []
    all_roles = detect_all_roles(rows, textmap)
    cols = _columns(rows)
    for key, role in all_roles.items():
        sample = cols[key][0]
        if is_text_hash(sample) and textmap:
            sample = textmap.get(str(sample["Hash"]), "<unresolved>")[:70]
        out.append(f"  {role.value:12s} <- {key:16s}  e.g. {str(sample)[:70]}")
    return "\n".join(out)


def is_placeholder_row(row: dict, textmap: dict, roles: dict[Role, str]) -> bool:
    """True for unshipped test rows, which carry '&&&' or empty display text.

    RoguePersonaStyle id 901 is one of these: a duplicate 'Camera Mask' whose
    flavour text is literally '&&&'. Nine masks ship; ten rows exist.
    """
    for role in (Role.NAME, Role.FLAVOUR, Role.TAGLINE):
        s = text(row, roles, role, textmap, default="")
        if s and ("&&&" in s or s.strip() in {"", "-"}):
            return True
    return False


def drop_placeholders(rows: Iterable[dict], textmap: dict, roles: dict[Role, str]) -> list[dict]:
    return [r for r in rows if not is_placeholder_row(r, textmap, roles)]


def role_histogram(rows: list[dict], textmap: dict | None = None) -> Counter:
    return Counter(detect_all_roles(rows, textmap).values())
