"""Join raw game tables into the single dataset the engine and UI consume.

    python -m data.build            # writes data/dataset.json + data/dataset.db

Everything here targets Tourn3 — Divergent Universe: Arcadian Chronicles. Older
themes are still present upstream and are deliberately filtered out.

The build asserts expected row counts at the end and raises on mismatch. A
silently wrong dataset would produce confident, wrong recommendations, which is
the exact failure mode this tool exists to prevent.
"""

from __future__ import annotations

import collections
import io
import json
import re
import sqlite3
import sys
from pathlib import Path

from data import options as doptions
from data import shapes
from data.fetch import load_table, load_textmap, CACHE, PIN_FILE
from data.shapes import Role
from data.tags import elements_in, mechanic_terms, tag_text
from data.text import plain, render

HERE = Path(__file__).parent
DATASET = HERE / "dataset.json"
DB = HERE / "dataset.db"

TOURN = "Tourn3"  # Arcadian Chronicles

# RogueTournBuff and RogueTournFormula carry every DU theme at once with no
# TournMode column, so the theme has to be recovered from id structure:
#
#   blessings  MazeBuffID prefix   615 / 616 / 617   (one series per theme)
#   equations  FormulaID leading digit  1 / 2 / 3
#
# The two line up: gen-3 equations draw on MazeBuff prefixes 676-678, disjoint
# from gen-1/2 (670-675), and gen-3's path set matches series 617 exactly.
# 617 shares 142 of its 144 names with 616 but 57 of those have *different*
# descriptions — it is the 4.0 rebalance of the previous theme. Building against
# the wrong series yields real names with stale numbers, so this is verified
# rather than assumed (see _detect_generation).
EXPECT = {
    "paths": 10,
    "theme_paths": 8,        # Arcadian Chronicles drops Preservation and Abundance
    "blessings": 144,
    "equations": 80,
    "path_echo": 8,
    "curios": 235,
    "weighted_curios": 17,
    "masks": 9,
    "mask_gifts": 286,       # Wishpower Miracles, across every Mask pool
    "mask_talents": 18,
}


class BuildError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _T(textmap: dict, v, default: str = "") -> str:
    """Resolve a {'Hash': n} reference, passing plain strings through."""
    if isinstance(v, dict) and "Hash" in v:
        return textmap.get(str(v["Hash"]), default)
    if isinstance(v, str):
        return v
    return default


def _maze_buff_index() -> dict[int, dict[int, dict]]:
    """Buff rows keyed by (buff id -> level -> row).

    Two tables, because the rogue-specific one does not hold everything DU
    references: weighted curios point at MazeBuffIDs 633401-633417 and
    `RogueMazeBuff` has no 6334xx bucket at all — it jumps from 6199xx to
    6340xx. Those rows are in the *generic* `MazeBuff` instead. The two share no
    (id, level) keys at the pinned commit, so this is a merge rather than a
    choice; `RogueMazeBuff` still wins any future collision, since a rogue-mode
    row is the more specific answer for a rogue-mode id.
    """
    idx: dict[int, dict[int, dict]] = {}
    for table in ("MazeBuff", "RogueMazeBuff"):
        for r in load_table(table):
            idx.setdefault(r["ID"], {})[r.get("Lv", 1)] = r
    return idx


def _param_values(row: dict) -> list[float]:
    return [p.get("Value") for p in row.get("ParamList", []) if isinstance(p, dict)]


def _detect_generation() -> tuple[str, str]:
    """Work out which blessing series and equation generation are the current theme.

    Newest is highest in both numbering schemes, but rather than trusting that,
    we cross-check: the set of Paths the newest equations reference must equal
    the set of Paths the newest blessing series covers. If a future patch breaks
    that assumption the build stops instead of quietly mixing two themes.
    """
    buffs = [r for r in load_table("RogueTournBuff") if r["MazeBuffLevel"] == 1]
    series = max({str(r["MazeBuffID"])[:3] for r in buffs})
    formulas = load_table("RogueTournFormula")
    gen = max({str(r["FormulaID"])[0] for r in formulas})

    blessing_paths = {r["RogueBuffType"] for r in buffs if str(r["MazeBuffID"]).startswith(series)}
    equation_paths: set[int] = set()
    for r in formulas:
        if str(r["FormulaID"])[0] != gen:
            continue
        equation_paths.add(r["MainBuffTypeID"])
        if r.get("SubBuffTypeID"):
            equation_paths.add(r["SubBuffTypeID"])

    if blessing_paths != equation_paths:
        raise BuildError(
            f"theme detection disagrees: blessing series {series} covers Paths "
            f"{sorted(blessing_paths)} but equation generation {gen} references "
            f"{sorted(equation_paths)}. Upstream renumbering — check data/build.py."
        )
    return series, gen


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

def build_paths(textmap: dict) -> tuple[list[dict], dict[int, str]]:
    """The 10 Paths, plus the profession-code aliases used by weighted curios.

    Weighted curios gate on internal profession codes ('Knight', 'Joy') which we
    recover from the icon filenames rather than hardcoding. The game's own data
    contains the typo 'Pirest', and some tables use the path name ('Elation')
    where others use the profession code ('Joy'), so both are registered.
    """
    paths, by_id = [], {}
    for r in load_table("RogueTournBuffType"):
        pid = r["RogueBuffType"]
        name = _T(textmap, r["RogueBuffTypeName"]).replace("{SPACE}", "").strip()
        m = re.search(r"IconProfession(\w+?)Middle", r.get("RogueBuffTypeIcon", ""))
        code = m.group(1) if m else None
        aliases = {name}
        if code:
            aliases.add(code)
            if code == "Pirest":       # upstream typo, seen in other tables as 'Priest'
                aliases.add("Priest")
            if code == "Joy":
                aliases.add("Elation")
        paths.append({
            "id": pid,
            "name": name,
            "profession_code": code,
            "aliases": sorted(aliases),
            "icon": r.get("RogueBuffTypeIcon", ""),
        })
        by_id[pid] = name
    return paths, by_id


def build_blessings(textmap: dict, path_by_id: dict[int, str], series: str) -> list[dict]:
    """One entry per blessing in the current theme's series."""
    mb = _maze_buff_index()
    grouped: dict[int, dict] = {}

    for r in load_table("RogueTournBuff"):
        buff_id, lv = r["MazeBuffID"], r["MazeBuffLevel"]
        if not str(buff_id).startswith(series):
            continue
        row = mb.get(buff_id, {}).get(lv)
        if row is None:
            continue
        params = _param_values(row)
        desc_tpl = _T(textmap, row.get("BuffDesc"))
        entry = grouped.setdefault(buff_id, {
            "id": buff_id,
            "kind": "blessing",
            "path_id": r["RogueBuffType"],
            "path": path_by_id.get(r["RogueBuffType"], "?"),
            "rarity": r["RogueBuffCategory"],
            "name": plain(_T(textmap, row.get("BuffName"))),
            "levels": {},
        })
        entry["levels"][lv] = {
            "level": lv,
            "params": params,
            "desc": render(desc_tpl, params),
            "desc_template": desc_tpl,
        }

    out = []
    for e in grouped.values():
        if not e["name"]:
            continue
        levels = [e["levels"][k] for k in sorted(e["levels"])]
        base = levels[0]
        e["levels"] = levels
        e["max_level"] = levels[-1]["level"]
        e["desc"] = base["desc"]
        e["search_text"] = plain(base["desc_template"])
        e["tags"] = tag_text(base["desc"], e["name"]) + mechanic_terms(base["desc"])
        e["elements"] = elements_in(base["desc"])
        e["upgrade_gain"] = _upgrade_gain(levels)
        out.append(e)
    return sorted(out, key=lambda x: (x["path_id"], x["rarity"], x["name"]))


def _upgrade_gain(levels: list[dict]) -> float | None:
    """Relative numeric gain from level 1 to max, for the Workbench factor.

    Compares the first parameter that actually moves between levels. Returns
    None when nothing scales (some blessings gain a clause instead of a number),
    in which case the engine falls back to a flat assumption.
    """
    if len(levels) < 2:
        return None
    first, last = levels[0]["params"], levels[-1]["params"]
    for a, b in zip(first, last):
        if a in (None, 0) or b is None:
            continue
        if b != a:
            return round((b - a) / abs(a), 4)
    return None


def build_equations(textmap: dict, path_by_id: dict[int, str], gen: str) -> list[dict]:
    mb = _maze_buff_index()
    display = {r["FormulaDisplayID"]: r for r in load_table("RogueTournFormulaDisplay")}
    out = []
    for r in load_table("RogueTournFormula"):
        if str(r["FormulaID"])[0] != gen:
            continue
        row = mb.get(r["MazeBuffID"], {}).get(1)
        if row is None:
            continue
        params = _param_values(row)
        desc_tpl = _T(textmap, row.get("BuffDesc"))
        name = plain(_T(textmap, row.get("BuffName")))
        if not name:
            continue
        requires = [{
            "path_id": r["MainBuffTypeID"],
            "path": path_by_id.get(r["MainBuffTypeID"], "?"),
            "count": r["MainBuffNum"],
        }]
        if r.get("SubBuffTypeID"):
            requires.append({
                "path_id": r["SubBuffTypeID"],
                "path": path_by_id.get(r["SubBuffTypeID"], "?"),
                "count": r.get("SubBuffNum", 0),
            })
        disp = display.get(r["FormulaDisplayID"], {})
        desc = render(desc_tpl, params)
        out.append({
            "id": r["FormulaID"],
            "kind": "equation",
            "name": name,
            "rarity": r["FormulaCategory"],
            "is_boundary": r["FormulaCategory"] == "PathEcho",
            "requires": requires,
            "total_required": sum(q["count"] for q in requires),
            "desc": desc,
            "search_text": plain(desc_tpl),
            "story": plain(_T(textmap, disp.get("FormulaStory"))),
            "tags": tag_text(desc, name) + mechanic_terms(desc),
            "elements": elements_in(desc),
        })
    return sorted(out, key=lambda x: (x["rarity"], x["name"]))


def build_curios(textmap: dict) -> list[dict]:
    """Tourn3 curios. Names resolve through the Tourn display table when present,
    otherwise through the shared RogueMiracleDisplay (which covers most of them)."""
    tourn_disp = {r["MiracleDisplayID"]: r for r in load_table("RogueTournMiracleDisplay")}
    shared_disp = {r["MiracleDisplayID"]: r for r in load_table("RogueMiracleDisplay")}
    # RogueTournMiracle.MiracleEffectID indexes **RogueMiracleEffect** (params in
    # ParamList). The similarly named RogueMiracleEffectDisplay is a different
    # table on a different id range — it stops at 1314 while every Tourn3 curio
    # points at 2001+, so joining against it yields nothing and leaves all 235
    # curios with empty descriptions.
    effects = {r["MiracleEffectID"]: r for r in load_table("RogueMiracleEffect")}

    out = []
    for r in load_table("RogueTournMiracle"):
        if r.get("TournMode") != TOURN:
            continue
        disp = tourn_disp.get(r["MiracleDisplayID"]) or shared_disp.get(r["MiracleDisplayID"]) or {}
        name = plain(_T(textmap, disp.get("MiracleName")))
        if not name:
            continue
        eff = effects.get(r.get("MiracleEffectID"), {})
        desc_tpl = _T(textmap, eff.get("MiracleDesc"))
        eff_params = [p.get("Value") for p in eff.get("ParamList", []) if isinstance(p, dict)]
        # bare=True: curio text mixes `#1[i]%` with spec-less `#2`, and both
        # index this ParamList (Ambergris Cheese uses one of each).
        desc = render(desc_tpl, eff_params, bare=True)
        bg = plain(_T(textmap, disp.get("MiracleBGDesc")))
        out.append({
            "id": r["MiracleID"],
            "kind": "curio",
            "name": name,
            "rarity": r["MiracleCategory"],
            "is_negative": r["MiracleCategory"] == "Negative",
            "desc": desc,
            "flavour": bg,
            "search_text": plain(desc_tpl) or bg,
            "tags": tag_text(desc, name),
            "elements": elements_in(desc),
            # Kept for link_curio_refs: `#{miracle:excel_3}` indexes this list,
            # and the value there is another curio's id.
            "params": eff_params,
            "icon": disp.get("MiracleIconPath", ""),
        })
    return sorted(out, key=lambda x: (x["rarity"], x["name"]))


def build_weighted_curios(textmap: dict, paths: list[dict]) -> list[dict]:
    """Tourn3 weighted curios, gated on character path and/or element.

    The gate values mix profession codes and path names, so both are normalised
    against the alias table derived in build_paths.
    """
    alias_to_path = {a: p["name"] for p in paths for a in p["aliases"]}
    mb = _maze_buff_index()
    disp = {r["HexDisplayID"]: r for r in load_table("RogueTournHexDisplay")}

    out = []
    for r in load_table("RogueTournHex"):
        if r.get("TournMode") != TOURN:
            continue
        d = disp.get(r["DisplayID"], {})
        name = plain(_T(textmap, d.get("Name"))).strip()
        row = mb.get(r["MazeBuffID"], {}).get(1, {})
        params = _param_values(row)
        desc = render(_T(textmap, row.get("BuffDesc")), params)

        raw_types = r.get("AvatarType") or []
        gate_paths = sorted({alias_to_path[a] for a in raw_types if a in alias_to_path})
        unknown = [a for a in raw_types if a not in alias_to_path]
        if unknown:
            raise BuildError(f"weighted curio {r['HexID']}: unmapped AvatarType {unknown}")

        out.append({
            "id": r["HexID"],
            "kind": "weighted_curio",
            "name": name,
            "desc": desc,
            "flavour": plain(_T(textmap, d.get("BgDesc"))),
            "search_text": plain(_T(textmap, row.get("BuffDesc"))),
            "gate_paths": gate_paths,
            "gate_elements": sorted(r.get("AvatarDamageType") or []),
            "tags": tag_text(desc, name) + mechanic_terms(desc),
        })
    return sorted(out, key=lambda x: x["name"])


def build_options(textmap: dict) -> list[dict]:
    """Occurrence / shop / Workbench dialogue options, classified by effect.

    Numbers in these texts are runtime placeholders ("#2 Cosmic Fragments") and
    the option row's ParamList does not line up with them, so no figures are
    invented here. The catalog supplies semantics; live values are read off the
    screen when a screenshot is scanned.
    """
    disp = {r["OptionDisplayID"]: r for r in load_table("RogueDialogueOptionDisplay")}
    seen: dict[int, dict] = {}

    for row in load_table("RogueDialogueOption"):
        did = row.get("OptionDisplayID")
        d = disp.get(did)
        if d is None or did in seen:
            continue
        title = plain(_T(textmap, d.get("OptionTitle")))
        desc = plain(_T(textmap, d.get("OptionDesc")))
        if not title and not desc:
            continue
        effects = doptions.classify(title, desc)
        seen[did] = {
            "id": did,
            "kind": "option",
            "name": title or desc[:60],
            "desc": desc,
            # Options in the same block appear on the same Occurrence — see
            # data/options.py:group_of. Used to offer the rest of the event once
            # you have identified one line of it.
            "group": doptions.group_of(did),
            "effects": effects,
            "context": doptions.context_of(title, desc),
            "risk": doptions.risk_level(effects),
            "pure_cost": doptions.is_pure_cost(effects),
            "templated": doptions.has_runtime_placeholder(desc),
            "search_text": f"{title} {desc}",
        }

    # Risk is a property of the *Occurrence*, not of one line of it. "Insert 200
    # Cosmic Fragments" reads as an ordinary purchase; only its siblings ("You
    # lost everything", "...lottery-type products") reveal it is a slot machine.
    # Marking the whole block lets the spend advice warn on the line you are
    # actually hovering over.
    gambling = {o["group"] for o in seen.values() if "gamble" in o["effects"]}
    for o in seen.values():
        o["group_gamble"] = o["group"] in gambling

    return sorted(seen.values(), key=lambda x: x["id"])


def build_domains(textmap: dict) -> tuple[list[dict], list[dict]]:
    """Domain (door) types and the beacon attributes that can be attached to them.

    These are what you choose between at a waypoint. `RoguePersonaRoomCompType`
    holds the 18 types — Combat, Occurrence, Wealth, Store, Reward, Respite,
    Elite, Boss and the hidden pink Escapades — and `RoguePersonaRoomAttribute`
    holds the 54 modifiers a door can carry (+100 Cosmic Fragments, +1 Blessing,
    a level increase, and so on).

    Both tables use obfuscated keys, so fields come from shape detection.
    """
    rows = load_table("RoguePersonaRoomCompType")
    roles = shapes.detect_roles(rows, textmap)
    shapes.require(roles, Role.ID, Role.NAME, table="RoguePersonaRoomCompType")

    types = []
    for r in rows:
        name = plain(shapes.text(r, roles, Role.NAME, textmap))
        if not name:
            continue
        desc = plain(shapes.text(r, roles, Role.FLAVOUR, textmap))
        types.append({
            "id": shapes.get(r, roles, Role.ID),
            "kind": "domain",
            "name": name,
            "colour": shapes.get(r, roles, Role.CATEGORY, ""),
            "desc": desc,
            "hidden": bool(shapes.get(r, roles, Role.FLAG, False)),
            "tags": tag_text(desc, name),
        })

    arows = load_table("RoguePersonaRoomAttribute")
    aroles = shapes.detect_roles(arows, textmap)
    shapes.require(aroles, Role.ID, Role.NAME, Role.EFFECT, table="RoguePersonaRoomAttribute")
    # Row 901 is the tutorial's copy of beacon 103, right down to the effect
    # text, and it says so in its own category column ("Tutorial", where every
    # shipped row reads Positive, Negative or Special). It reached the door
    # beacon picker as a second identical "Curio" and scored as a positive,
    # since `waypoint` reads anything that is not Negative as one.
    arows = shapes.drop_dev_rows(arows, aroles)

    attrs = []
    for r in arows:
        p = shapes.params(r, aroles)
        eff = render(shapes.text(r, aroles, Role.EFFECT, textmap), p)
        name = plain(shapes.text(r, aroles, Role.NAME, textmap))
        if not name:
            continue
        attrs.append({
            "id": shapes.get(r, aroles, Role.ID),
            "kind": "beacon",
            "name": name,
            "effect": eff,
            "polarity": shapes.get(r, aroles, Role.CATEGORY, "Positive"),
            "params": p,
            "effects": doptions.classify(name, eff),
        })

    return types, attrs


def build_deck(domains: list[dict]) -> dict:
    """What the waypoint draw actually draws from.

    `RoguePersonaConstCommon` is one of the few Persona tables shipped with plain
    keys. It names the Domain types the deck randomises over
    (`RogueTournPersona_RandomCompList`), the ones that are always placed
    (`_FixedCompList` — Boss, Respite, Conversion), and the maximum hand size
    (`_MaxDrawCount`, 5).

    This is what lets the redraw advice be grounded: the alternative to the hand
    in front of you is a draw from *these* types, not from an invented average.
    It is still an approximation — the engine cannot see which cards are left in
    your Reserve Pile — and the advice says so.
    """
    by_id = {d["id"]: d["name"] for d in domains}
    consts = {}
    for r in load_table("RoguePersonaConstCommon"):
        name, value = r.get("ConstValueName"), r.get("Value") or {}
        if "ArrayValue" in value:
            consts[name] = [v.get("IntValue") for v in value["ArrayValue"]]
        elif "IntValue" in value:
            consts[name] = value["IntValue"]

    def names(key: str) -> list[str]:
        return [by_id[i] for i in consts.get(key, []) if i in by_id]

    return {
        "random_types": names("RogueTournPersona_RandomCompList"),
        "fixed_types": names("RogueTournPersona_FixedCompList"),
        "max_draw": consts.get("RogueTournPersona_MaxDrawCount", 5),
    }


def build_run_lengths() -> list[dict]:
    """The possible run layouts, derived rather than assumed.

    `RoguePersonaLayerRoom` lists the steps (Domains) in each layer (Plane), and
    `RogueTournArea` says which layers a given difficulty draws on. They group
    into three variants:

        3001+3002+3003 =  4+5+4 = 13 Domains
        3011+3012+3013 =  5+7+5 = 17 Domains
        3021+3022+3023 =  6+8+6 = 20 Domains

    Difficulty 1-4 only ever uses the 13-Domain layout; Difficulty 5 can draw any
    of the three. Every variant is three layers, so Arcadian Chronicles runs
    three Planes, not four.
    """
    rows = load_table("RoguePersonaLayerRoom")
    lroles = shapes.detect_roles(rows, None)
    steps: dict[int, int] = collections.Counter()
    for r in rows:
        lid = shapes.get(r, lroles, Role.ID)
        if isinstance(lid, int):
            steps[lid] += 1

    # Difficulty -> layers, from RogueTournArea. Its keys are obfuscated and it
    # carries text hashes, so rather than full role detection we just pick out
    # the "Difficulty_N" string and the integer list on each row.
    area = load_table("RogueTournArea")
    diff_layers: dict[str, set[int]] = collections.defaultdict(set)
    for r in area:
        diff = None
        layers: list[int] = []
        for v in r.values():
            if isinstance(v, str) and v.startswith("Difficulty_"):
                diff = v
            elif isinstance(v, list) and v and all(isinstance(x, int) for x in v):
                layers = v
        if diff:
            diff_layers[diff].update(l for l in layers if l in steps and l >= 3000)

    variants: dict[str, list[int]] = collections.defaultdict(list)
    for lid in steps:
        if lid >= 3000:
            variants[str(lid)[:3]].append(lid)

    out = []
    for prefix, layers in sorted(variants.items()):
        layers = sorted(layers)
        total = sum(steps[l] for l in layers)
        difficulties = sorted(
            int(d.split("_")[1]) for d, ls in diff_layers.items() if set(layers) & ls
        )
        out.append({
            "variant": prefix,
            "layers": layers,
            "steps": [steps[l] for l in layers],
            "planes": len(layers),
            "domains": total,
            "difficulties": difficulties,
        })
    return sorted(out, key=lambda x: x["domains"])


def build_event_catalog(textmap: dict) -> list[dict]:
    """Named Occurrences, for recognising which event you are looking at."""
    out = []
    for r in load_table("RogueTournHandBookEvent"):
        if str(r.get("IsUsed", "True")).lower() == "false":
            continue
        title = plain(_T(textmap, r.get("EventTitle")))
        if not title:
            continue
        out.append({"id": int(r["EventHandbookID"]), "kind": "event", "name": title})
    return sorted(out, key=lambda x: x["name"])


def build_characters(textmap: dict, paths: list[dict]) -> list[dict]:
    """Playable characters, so team comp can be picked by name rather than typed.

    AvatarBaseType is the same profession-code vocabulary the weighted curios
    gate on, so it maps through the alias table. The several Trailblazer rows all
    render as '{NICKNAME}' and are disambiguated by their Path.

    Two kinds of row here are not playable characters and have to go:

    * **Names carrying rich-text markup.** `AvatarName` is TextMap like any other
      string, so it can contain `<unbreak>` and `<color=…>`. Everywhere else that
      is stripped by `text.plain`; this join never did, which is how "Silver Wolf
      LV.<unbreak>999</unbreak>" reached the character list as though it were
      somebody you could pick.
    * **Level-suffixed variants.** That same row is the giveaway: a name ending
      in `LV.999` is a combat-preview or NPC entry, not somebody you can bring
      in. Same family as `RoguePersonaStyle` id 901's `&&&` placeholder, and
      dropped for the same reason.
    """
    alias_to_path = {a: p["name"] for p in paths for a in p["aliases"]}
    out, seen = [], set()
    for r in load_table("AvatarConfig"):
        base = r.get("AvatarBaseType")
        path = alias_to_path.get(base)
        if not path:
            continue
        name = plain(_T(textmap, r.get("AvatarName"))).strip()
        if not name or re.search(r"\bLv\.?\s*\d+\s*$", name, re.I):
            continue
        if "{NICKNAME}" in name:
            name = f"Trailblazer ({path})"
        if name in seen:      # multiple rows per Trailblazer Path variant
            continue
        seen.add(name)
        out.append({
            "id": r["AvatarID"],
            "name": name,
            "path": path,
            "element": r.get("DamageType", ""),
            "rarity": 5 if r.get("Rarity", "").endswith("5") else 4,
        })
    return sorted(out, key=lambda x: x["name"])


def build_masks(textmap: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Masks, their Wishpower talents, and the gift pool — all obfuscated tables."""
    style_rows = load_table("RoguePersonaStyle")
    roles = shapes.detect_roles(style_rows, textmap)
    shapes.require(roles, Role.ID, Role.NAME, Role.EFFECT, Role.PARAMS, table="RoguePersonaStyle")
    style_rows = shapes.drop_placeholders(style_rows, textmap, roles)

    masks = []
    for r in style_rows:
        params = shapes.params(r, roles)
        masks.append({
            "id": shapes.get(r, roles, Role.ID),
            "kind": "mask",
            "name": plain(shapes.text(r, roles, Role.NAME, textmap)),
            "tagline": plain(shapes.text(r, roles, Role.TAGLINE, textmap)),
            "flavour": plain(shapes.text(r, roles, Role.FLAVOUR, textmap)),
            "effect": render(shapes.text(r, roles, Role.EFFECT, textmap), params),
            "wishpower": render(shapes.text(r, roles, Role.EFFECT_ALT, textmap), params),
            "params": params,
            "gift_ids": shapes.get(r, roles, Role.REFS) or [],
            "icon": shapes.get(r, roles, Role.ICON, ""),
        })
        masks[-1]["tags"] = tag_text(masks[-1]["effect"], masks[-1]["tagline"])

    # talents
    trows = load_table("RoguePersonaTalent")
    troles = shapes.detect_roles(trows, textmap)
    shapes.require(troles, Role.ID, Role.NAME, Role.EFFECT, table="RoguePersonaTalent")
    groups = {}
    grows = load_table("RoguePersonaTalentGroup")
    groles = shapes.detect_roles(grows, textmap)
    for g in grows:
        groups[shapes.get(g, groles, Role.ID)] = shapes.text(g, groles, Role.NAME, textmap)

    talents = []
    for r in trows:
        p = shapes.params(r, troles)
        talents.append({
            "id": shapes.get(r, troles, Role.ID),
            "kind": "mask_talent",
            "group_id": shapes.get(r, troles, Role.GROUP),
            "group": groups.get(shapes.get(r, troles, Role.GROUP), ""),
            "level": shapes.get(r, troles, Role.LEVEL, 1),
            "name": plain(shapes.text(r, troles, Role.NAME, textmap)),
            "effect": render(shapes.text(r, troles, Role.EFFECT, textmap), p),
            "params": p,
        })

    # gifts
    grows2 = load_table("RoguePersonaStyleGift")
    g2 = shapes.detect_roles(grows2, textmap)
    shapes.require(g2, Role.ID, Role.NAME, Role.EFFECT, table="RoguePersonaStyleGift")
    # Shipped gifts run 101-239 and 601-788. Row 901 is alone above that and is
    # a copy of gift 176 with an empty Mask list, so `universal` put it in every
    # Mask's pool: the Wishpower tab listed the same "Attaches 1 random beacon
    # to 1 designated Domain(s)" twice, with nothing to choose between them.
    grows2 = shapes.drop_dev_rows(grows2, g2)
    gifts = []
    for r in grows2:
        p = shapes.params(r, g2)
        eff = render(shapes.text(r, g2, Role.EFFECT, textmap), p)
        ids = shapes.get(r, g2, Role.REFS) or []
        gifts.append({
            "id": shapes.get(r, g2, Role.ID),
            # These are the "Miracles" offered when Wishpower levels up. The
            # table calls them gifts; the game calls them Miracles, and so does
            # every surface the player sees.
            "kind": "miracle",
            "name": plain(shapes.text(r, g2, Role.NAME, textmap)),
            "effect": eff,
            "rarity": shapes.get(r, g2, Role.CATEGORY, "Common"),
            "mask_ids": ids,
            "universal": not ids,
            "params": p,
            "tags": tag_text(eff),
        })

    return masks, talents, gifts


# --------------------------------------------------------------------------

ROOM_REF_RE = re.compile(r'#\{(room_comp_type|room_attribute):(\d+)\}')
CURIO_REF_RE = re.compile(r'#\{miracle:excel_(\d+)\}')


def link_room_refs(rows: list[dict], domains: list[dict], beacons: list[dict],
                   fields: tuple[str, ...] = ("effect",), refresh=None) -> int:
    """Resolve `#{room_comp_type:3}` / `#{room_attribute:105}` in run text.

    Miracles are almost entirely about the Domain deck, so their text is full of
    these references — 258 of them across the pool, and every single one is
    unreadable until it is joined. `room_comp_type` indexes the Domain types from
    `RoguePersonaRoomCompType` and `room_attribute` the beacons from
    `RoguePersonaRoomAttribute`, both of which are already built here.

    Miracles are not the only pool that carries them: 46 curios do too, and there
    the placeholders are just as load-bearing — "remove 1 #{room_comp_type:2}
    Domain" is advice about your Elites or about nothing. Hence `fields`, since
    the curio text lives in `desc`/`search_text` rather than `effect`.

    The referenced ids are recorded alongside the text so the engine can score a
    named beacon by its real polarity rather than by guessing from wording.
    `refresh` re-derives whatever a row computes from its own text; the default
    re-tags off the first field. Returns the number of references left unresolved.
    """
    dom = {d["id"]: d["name"] for d in domains}
    bea = {b["id"]: b for b in beacons}
    unresolved = 0

    for row in rows:
        domain_refs: list[str] = []
        beacon_refs: list[int] = []
        missing: set[tuple[str, int]] = set()

        def sub(m: re.Match) -> str:
            table, ref = m.group(1), int(m.group(2))
            if table == "room_comp_type":
                name = dom.get(ref)
                if name and name not in domain_refs:
                    domain_refs.append(name)
            else:
                name = (bea.get(ref) or {}).get("name")
                if name and ref not in beacon_refs:
                    beacon_refs.append(ref)
            if not name:
                # Counted per distinct reference, not per occurrence: the same
                # id appears in both `desc` and `search_text`.
                missing.add((table, ref))
                return m.group(0)
            return name

        for f in fields:
            if row.get(f):
                row[f] = ROOM_REF_RE.sub(sub, row[f])
        row["domain_refs"] = domain_refs
        row["beacon_refs"] = beacon_refs
        unresolved += len(missing)
        # Re-derive: the resolved names carry vocabulary the placeholders hid.
        if refresh:
            refresh(row)
        else:
            row["tags"] = tag_text(row[fields[0]])

    return unresolved


def link_curio_refs(curios: list[dict], fields: tuple[str, ...] = ("desc", "search_text")) -> int:
    """Resolve `#{miracle:excel_3}` — a curio naming another curio.

    The digit is a 1-based index into the effect row's `ParamList`, and the value
    sitting there is a curio id rather than a number: `Ambergris Cheese` param 3
    is 9063, which is `King of Sponges`. Eight references across six curios, and
    every one of them is the *outcome* — what this upgrades into, or which three
    curios it hands you — so leaving them raw hides the entire payoff.

    Runs before `link_room_refs`, so the names it substitutes are in the text by
    the time tags are re-derived. Returns the number left unresolved.
    """
    names = {c["id"]: c["name"] for c in curios}
    unresolved = 0

    for c in curios:
        params = c.get("params") or []
        refs: list[int] = []
        missing: set[int] = set()

        def sub(m: re.Match) -> str:
            i = int(m.group(1))
            v = params[i - 1] if 0 < i <= len(params) else None
            name = names.get(int(v)) if isinstance(v, (int, float)) else None
            if not name:
                missing.add(i)
                return m.group(0)
            if int(v) not in refs:
                refs.append(int(v))
            return name

        for f in fields:
            if c.get(f):
                c[f] = CURIO_REF_RE.sub(sub, c[f])
        c["curio_refs"] = refs
        unresolved += len(missing)

    return unresolved


# --------------------------------------------------------------------------
# search index
# --------------------------------------------------------------------------

def build_index(dataset: dict) -> None:
    """SQLite FTS5 index over names and descriptions.

    Both the manual search box and the OCR resolver query this, so typing
    "contrib" and OCR reading "Contrib..." take the same code path.
    """
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE VIRTUAL TABLE entries USING fts5(
            kind, entry_id UNINDEXED, name, desc, path, rarity,
            tokenize='porter unicode61'
        )
    """)
    rows = []
    for kind in ("blessings", "equations", "curios", "weighted_curios", "masks",
                 "options", "mask_gifts"):
        for e in dataset[kind]:
            rows.append((
                e["kind"], str(e["id"]), e.get("name", ""),
                e.get("desc", "") or e.get("effect", ""),
                e.get("path", ""), e.get("rarity", ""),
            ))
    con.executemany("INSERT INTO entries VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def _twins(rows: list[dict], fields: tuple[str, ...],
           list_field: str | None = None) -> list[tuple]:
    """Id pairs for rows a reader has no way to tell apart.

    Everything the player sees about a Miracle or a beacon is in `fields`, so two
    rows agreeing on all of them are one row twice as far as any surface of this
    app is concerned, whatever the ids say.
    """
    seen: dict[tuple, object] = {}
    pairs = []
    for r in rows:
        key = tuple(r.get(f) for f in fields)
        if list_field:
            key += (tuple(r.get(list_field) or ()),)
        first = seen.setdefault(key, r["id"])
        if first != r["id"]:
            pairs.append((first, r["id"]))
    return pairs


def verify(ds: dict) -> list[str]:
    """Return a list of problems; empty means the dataset looks sane."""
    problems = []

    def check(label: str, got: int, want: int, exact: bool = True):
        ok = got == want if exact else got >= want
        if not ok:
            problems.append(f"{label}: expected {'' if exact else '>='}{want}, got {got}")

    check("paths", len(ds["paths"]), EXPECT["paths"])
    check("theme_paths", sum(p["in_theme"] for p in ds["paths"]), EXPECT["theme_paths"])
    check("blessings", len(ds["blessings"]), EXPECT["blessings"])
    check("equations", len(ds["equations"]), EXPECT["equations"])
    check("path_echo", sum(e["is_boundary"] for e in ds["equations"]), EXPECT["path_echo"])
    check("curios", len(ds["curios"]), EXPECT["curios"])
    check("weighted_curios", len(ds["weighted_curios"]), EXPECT["weighted_curios"])
    check("masks", len(ds["masks"]), EXPECT["masks"])
    check("mask_gifts", len(ds["mask_gifts"]), EXPECT["mask_gifts"])
    check("mask_talents", len(ds["mask_talents"]), EXPECT["mask_talents"])

    # Miracle text is mostly Domain-deck manipulation, so an unresolved
    # `#{room_comp_type:5}` is not cosmetic — it is the entire content of the
    # line, and the player would be asked to choose between three lines they
    # cannot read.
    leaked = [g["id"] for g in ds["mask_gifts"] if "#{" in g["effect"]]
    if leaked:
        problems.append(
            f"mask_gifts: {len(leaked)} unresolved room references, e.g. id {leaked[0]}")
    nameless = [g["id"] for g in ds["mask_gifts"] if not g["effect"]]
    if nameless:
        problems.append(f"mask_gifts: {len(nameless)} have no effect text")

    # Two rows a reader cannot tell apart are a choice that does not exist. The
    # Wishpower pool is browsed rather than searched, precisely because 136 of
    # these share three names between them, so the effect text is the only thing
    # separating one row from the next — and gift 901 matched another row in all
    # of it. The id rule in `build_masks` drops the one we know about; this is
    # what catches the next patch's, whatever id it lands on.
    twinned = _twins(ds["mask_gifts"], ("name", "rarity", "effect"), "mask_ids")
    if twinned:
        problems.append(
            f"mask_gifts: {len(twinned)} pairs are identical in name, rarity, effect "
            f"and Mask list, e.g. ids {twinned[0]}")

    # Same for the door beacons, which are picked off a list of names.
    twinned = _twins(ds["beacons"], ("name", "effect", "polarity"))
    if twinned:
        problems.append(
            f"beacons: {len(twinned)} pairs are identical in name, effect and "
            f"polarity, e.g. ids {twinned[0]}")

    # Same for curios, and for the same reason — a curio that removes your
    # "#{room_comp_type:2}" Domains is a ranked card the player cannot read.
    leaked = [c["id"] for c in ds["curios"] if "#{" in c["desc"]]
    if leaked:
        problems.append(
            f"curios: {len(leaked)} unresolved room references, e.g. id {leaked[0]}")

    # Weighted curios had no check at all, which is how all 17 shipped with an
    # empty desc for months: their MazeBuffIDs are not in RogueMazeBuff, the join
    # returned {}, and nothing complained. The gate still worked, so the UI kept
    # saying useful things about *who can trigger* them while knowing nothing
    # about what they do. Small collection, so nothing is tolerated here.
    blank = [w["id"] for w in ds["weighted_curios"] if not w["desc"]]
    if blank:
        problems.append(
            f"weighted_curios: {len(blank)} of {len(ds['weighted_curios'])} have no "
            f"effect text, e.g. id {blank[0]} — check the MazeBuff join")
    leaked = [w["id"] for w in ds["weighted_curios"] if "#{" in w["desc"]]
    if leaked:
        problems.append(
            f"weighted_curios: {len(leaked)} unresolved references, e.g. id {leaked[0]}")

    # Options carry no resolvable params — their numbers are runtime figures —
    # so every placeholder must have become the "N" marker. A raw one reaching a
    # Spend card reads as a bug rather than as "some number": 640 shipped as
    # "Consumes #2 Cosmic Fragments" before this check existed. Deliberately for
    # any `#` and not the `#N` shape specifically, exactly as the curio check is,
    # so a new placeholder family stops the build instead of reaching a card.
    leaked = [o["id"] for o in ds["options"] if "#" in (o.get("desc") or "")]
    if leaked:
        problems.append(
            f"options: {len(leaked)} unrendered placeholders, e.g. id {leaked[0]}")

    if {p["id"] for p in ds["paths"]} != set(range(120, 130)):
        problems.append("paths: expected ids 120..129")

    # A blessing whose Path the theme does not offer means the series filter leaked.
    in_theme = {p["id"] for p in ds["paths"] if p["in_theme"]}
    stray = {b["path"] for b in ds["blessings"] if b["path_id"] not in in_theme}
    if stray:
        problems.append(f"blessings: found Paths outside the theme: {sorted(stray)}")

    # Every blessing name must be unique within the theme, or the resolver cannot
    # disambiguate. Cross-theme duplicates are expected and filtered out above.
    names = collections.Counter(b["name"] for b in ds["blessings"])
    dupes = [n for n, c in names.items() if c > 1]
    if dupes:
        problems.append(f"blessings: {len(dupes)} duplicate names within theme, e.g. {dupes[:3]}")

    # A curio with no effect text is one the engine cannot tag, cannot score and
    # cannot show you. This silently affected every curio in the set once, so it
    # is checked rather than assumed.
    no_effect = [c["name"] for c in ds["curios"] if not c["desc"]]
    if len(no_effect) > len(ds["curios"]) * 0.05:
        problems.append(
            f"curios: {len(no_effect)} of {len(ds['curios'])} have no effect text — "
            f"the RogueMiracleEffect join is broken, e.g. {no_effect[:3]}")

    untagged = [b["name"] for b in ds["blessings"] if not b["tags"]]
    if len(untagged) > len(ds["blessings"]) * 0.15:
        problems.append(f"tagging: {len(untagged)} blessings got no mechanic tag — patterns may be stale")

    if not ds.get("run_lengths"):
        problems.append("run_lengths: none derived — layer tables may have changed")
    else:
        totals = {v["domains"] for v in ds["run_lengths"]}
        if not totals & {13, 17, 20}:
            problems.append(f"run_lengths: unexpected totals {sorted(totals)}")
        for v in ds["run_lengths"]:
            if v["planes"] != 3:
                problems.append(f"run_lengths: variant {v['variant']} has {v['planes']} Planes, expected 3")

    if len(ds["domains"]) < 12:
        problems.append(f"domains: only {len(ds['domains'])} types found, expected 12+")
    if len(ds["beacons"]) < 40:
        problems.append(f"beacons: only {len(ds['beacons'])} found, expected 40+")

    deck = ds.get("deck") or {}
    if len(deck.get("random_types") or []) < 8:
        problems.append(
            f"deck: only {len(deck.get('random_types') or [])} random Domain types resolved "
            f"from RoguePersonaConstCommon — the redraw estimate has no basis")
    if not deck.get("fixed_types"):
        problems.append("deck: no fixed Domain types resolved")

    if len(ds["options"]) < 900:
        problems.append(f"options: only {len(ds['options'])} classified, expected 900+")
    unclassified = [o for o in ds["options"] if not o["effects"]]
    if len(unclassified) > len(ds["options"]) * 0.35:
        problems.append(f"options: {len(unclassified)} have no effect tags — patterns may be stale")

    if len(ds["characters"]) < 60:
        problems.append(f"characters: only {len(ds['characters'])} resolved, expected 60+")

    # A character is picked by eye against the game's own team screen, so a name
    # that is not a plain name is not merely ugly — it is a row that should never
    # have been offered. Fail the build rather than ship one, exactly as with the
    # curio placeholder check above.
    marked_up = [c["name"] for c in ds["characters"] if re.search(r"[<>{}]", c["name"])]
    if marked_up:
        problems.append(f"characters: {len(marked_up)} name(s) carry markup: {marked_up[:3]}")

    for e in ds["equations"]:
        for req in e["requires"]:
            if req["path"] == "?":
                problems.append(f"equation {e['name']}: unresolved path id {req['path_id']}")
                break
    return problems


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not (CACHE / "TextMap" / "TextMapEN.json").exists():
        raise SystemExit("No cache. Run `python -m data.fetch` first.")

    print("loading TextMapEN ...", end="", flush=True)
    textmap = load_textmap()
    print(f" {len(textmap):,} strings")

    series, gen = _detect_generation()
    print(f"theme: blessing series {series}, equation generation {gen}")

    paths, path_by_id = build_paths(textmap)
    blessings = build_blessings(textmap, path_by_id, series)
    equations = build_equations(textmap, path_by_id, gen)
    curios = build_curios(textmap)

    # Mark which Paths this theme actually offers. Arcadian Chronicles has no
    # Preservation or Abundance blessings at all, which the engine must know:
    # chasing an equation that needs them is impossible, not merely expensive.
    offered = {b["path_id"] for b in blessings}
    for p in paths:
        p["in_theme"] = p["id"] in offered
    weighted = build_weighted_curios(textmap, paths)
    masks, talents, gifts = build_masks(textmap)
    characters = build_characters(textmap, paths)
    dialogue_options = build_options(textmap)
    event_catalog = build_event_catalog(textmap)
    domain_types, beacons = build_domains(textmap)
    deck = build_deck(domain_types)
    run_lengths = build_run_lengths()

    # Miracles and curios reference Domain types and beacons by id; join them now
    # that both tables are built, or the text reads as "#{room_comp_type:3}".
    def _refresh_curio(c: dict) -> None:
        c["tags"] = tag_text(c["desc"], c["name"])
        c["elements"] = elements_in(c["desc"])

    left = link_curio_refs(curios)
    left += link_room_refs(gifts, domain_types, beacons)
    left += link_room_refs(curios, domain_types, beacons,
                           fields=("desc", "search_text"), refresh=_refresh_curio)
    if left:
        print(f"  warning: {left} room references could not be resolved")

    pin = json.loads(PIN_FILE.read_text(encoding="utf-8"))
    dataset = {
        "meta": {
            "tourn_mode": TOURN,
            "theme": "Divergent Universe: Arcadian Chronicles",
            "source_sha": pin["sha"],
            "source_title": pin["title"],
            "blessing_series": series,
            "equation_generation": gen,
        },
        "paths": paths,
        "blessings": blessings,
        "equations": equations,
        "curios": curios,
        "weighted_curios": weighted,
        "masks": masks,
        "mask_talents": talents,
        "mask_gifts": gifts,
        "characters": characters,
        "options": dialogue_options,
        "events": event_catalog,
        "domains": domain_types,
        "beacons": beacons,
        "deck": deck,
        "run_lengths": run_lengths,
    }

    problems = verify(dataset)
    for kind in ("paths", "blessings", "equations", "curios", "weighted_curios", "masks",
                 "mask_talents", "mask_gifts", "characters", "options", "events", "domains", "beacons"):
        print(f"  {kind:16s} {len(dataset[kind]):>5}")
    print(f"  {'boundary eqs':16s} {sum(e['is_boundary'] for e in equations):>5}")

    if problems:
        print("\nFAILED verification:")
        for p in problems:
            print(f"  - {p}")
        raise BuildError("dataset failed verification; refusing to write")

    with io.open(DATASET, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=1)
    build_index(dataset)
    print(f"\nwrote {DATASET} ({DATASET.stat().st_size:,} bytes)")
    print(f"wrote {DB} ({DB.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
