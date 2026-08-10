"""Pull Honkai: Star Rail game data tables from the upstream GitLab mirror.

The upstream repo moved to GitLab after the October 2024 DMCA takedown of the
GitHub original. We pin a specific commit SHA rather than tracking `main`: an
upstream push mid-patch would otherwise silently change what the scoring engine
recommends, with no signal that anything moved.

    python -m data.fetch              # fetch pinned revision into data/cache/
    python -m data.fetch --update     # move the pin to upstream HEAD, report the diff
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.parse
from pathlib import Path

import requests

PROJECT = urllib.parse.quote_plus("Dimbreath/turnbasedgamedata")
API = f"https://gitlab.com/api/v4/projects/{PROJECT}"
RAW = "https://gitlab.com/Dimbreath/turnbasedgamedata/-/raw"

HERE = Path(__file__).parent
CACHE = HERE / "cache"
PIN_FILE = HERE / "pinned_revision.json"

# Divergent Universe lives in the RogueTourn* namespace. Masks are RoguePersona*
# (the sprite paths are literally SpriteOutput/Rogue/Tourn/Persona/...).
# RogueMazeBuff is shared across all rogue modes and holds the actual display
# name, description template and numeric ParamList for every blessing.
EXCEL_TABLES = [
    # blessings
    "RogueTournBuff",
    "RogueTournBuffType",
    "RogueTournBuffGroup",
    "RogueMazeBuff",
    # equations
    "RogueTournFormula",
    "RogueTournFormulaDisplay",
    "RogueTournFormulaRandom",
    # Curios. RogueTournMiracleDisplay covers only 166 entries; most Tourn3
    # curios resolve their name through the *shared* RogueMiracleDisplay
    # instead, so both are required to name all 235.
    "RogueTournMiracle",
    "RogueTournMiracleDisplay",
    "RogueTournMiracleGroup",
    "RogueTournHandbookMiracle",
    "RogueMiracle",
    "RogueMiracleDisplay",
    "RogueMiracleEffectDisplay",
    # Curio *effects*. RogueTournMiracle.MiracleEffectID points here, not at
    # RogueMiracleEffectDisplay — that table stops at id 1314 and the Tourn3
    # curios all sit at 2001+, so the join silently produced 235 curios with no
    # effect text at all until this table was added.
    "RogueMiracleEffect",
    # Weighted curios. RogueTournHex is the Tourn3 set (17 entries);
    # RogueTournHexAvatarBaseType is the legacy Tourn1/Tourn2 gating table and
    # shares no ids with Tourn3 — kept only for reference.
    "RogueTournHex",
    "RogueTournHexDisplay",
    "RogueTournHexAvatarBaseType",
    # ...and their *effects*, which are not in RogueMazeBuff. RogueTournHex
    # points at MazeBuffIDs 633401-633417, and RogueMazeBuff jumps straight from
    # 6199xx to 6340xx — there is no 6334xx bucket in it at all. They live in the
    # generic, non-rogue MazeBuff table instead, so without this every one of the
    # 17 built with an empty desc, empty search_text and no tags: gate checks
    # still worked, but nothing could be scored on what it actually does.
    "MazeBuff",
    # masks
    "RoguePersonaStyle",
    "RoguePersonaTalent",
    "RoguePersonaTalentGroup",
    "RoguePersonaStyleGift",
    # run structure
    "RogueTournWorkbench",
    "RogueTournWorkbenchFunc",
    "RogueTournDifficulty",
    "RogueTournDifficultyComp",
    "RogueTournWeeklyChallenge",
    "RogueTournTitanBless",
    "RogueTournTitanType",
    "RogueTournKeyword",
    "RogueTournKeywordParam",
    "RogueTournRoom",
    "RogueTournArea",
    # The Domain deck you draw from at a waypoint: the Domain types themselves,
    # the beacons that can be attached to them, the per-Plane step counts that
    # give the run its length, and the constants naming which Domain types the
    # deck randomises over. All obfuscated-key tables — see data/shapes.py.
    "RoguePersonaRoomCompType",
    "RoguePersonaRoomAttribute",
    "RoguePersonaLayerRoom",
    "RoguePersonaConstCommon",
    # Characters, event options and the event handbook.
    "AvatarConfig",
    "RogueDialogueOption",
    "RogueDialogueOptionDisplay",
    "RogueTournHandBookEvent",
]

TEXTMAPS = ["TextMapEN"]


def pinned_sha() -> str:
    if not PIN_FILE.exists():
        raise SystemExit(
            "No pinned revision. Run `python -m data.fetch --update` to create one."
        )
    return json.loads(PIN_FILE.read_text(encoding="utf-8"))["sha"]


def upstream_head() -> dict:
    r = requests.get(f"{API}/repository/commits", params={"ref_name": "main", "per_page": 1}, timeout=60)
    r.raise_for_status()
    c = r.json()[0]
    return {"sha": c["id"], "created_at": c["created_at"], "title": c["title"]}


def _download(sha: str, repo_path: str, dest: Path) -> None:
    url = f"{RAW}/{sha}/{repo_path}"
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        tmp.replace(dest)


def fetch_all(sha: str, force: bool = False) -> None:
    """Download every table we need at `sha`, skipping files already cached."""
    jobs = [(f"ExcelOutput/{t}.json", CACHE / "ExcelOutput" / f"{t}.json") for t in EXCEL_TABLES]
    jobs += [(f"TextMap/{t}.json", CACHE / "TextMap" / f"{t}.json") for t in TEXTMAPS]

    for repo_path, dest in jobs:
        if dest.exists() and not force:
            continue
        print(f"  fetching {repo_path} ...", end="", flush=True)
        _download(sha, repo_path, dest)
        print(f" {dest.stat().st_size:,} bytes")


def load_table(name: str) -> list | dict:
    """Load a cached ExcelOutput table.

    Always explicit utf-8: the game text contains characters that blow up the
    Windows cp1252 default codec.
    """
    path = CACHE / "ExcelOutput" / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"{name}.json not cached. Run `python -m data.fetch` first.")
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def load_textmap(lang: str = "TextMapEN") -> dict:
    path = CACHE / "TextMap" / f"{lang}.json"
    if not path.exists():
        raise SystemExit(f"{lang}.json not cached. Run `python -m data.fetch` first.")
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _id_set(table_name: str) -> set:
    """Best-effort set of primary IDs in a table, for pin-bump diffing."""
    try:
        rows = load_table(table_name)
    except SystemExit:
        return set()
    if not isinstance(rows, list) or not rows:
        return set()
    # The first integer-valued field is the primary ID in every table we touch,
    # including the obfuscated ones where we cannot rely on the key's name.
    first_int_key = next((k for k, v in rows[0].items() if isinstance(v, int)), None)
    if first_int_key is None:
        return set()
    return {r.get(first_int_key) for r in rows if isinstance(r.get(first_int_key), int)}


def update_pin() -> None:
    head = upstream_head()
    old = None
    if PIN_FILE.exists():
        old = json.loads(PIN_FILE.read_text(encoding="utf-8"))

    if old and old["sha"] == head["sha"]:
        print(f"Already pinned to upstream HEAD {head['sha'][:12]} ({head['title']}).")
        return

    before = {t: _id_set(t) for t in EXCEL_TABLES} if old else {}

    print(f"Upstream HEAD: {head['sha'][:12]}  {head['created_at']}  {head['title']}")
    if old:
        print(f"Current pin:   {old['sha'][:12]}  {old.get('title', '?')}")

    fetch_all(head["sha"], force=True)
    PIN_FILE.write_text(json.dumps(head, indent=2) + "\n", encoding="utf-8")

    if before:
        print("\nID diff vs previous pin:")
        changed = False
        for t in EXCEL_TABLES:
            after = _id_set(t)
            added, removed = after - before[t], before[t] - after
            if added or removed:
                changed = True
                print(f"  {t}: +{len(added)} -{len(removed)}")
                if added:
                    print(f"      added:   {sorted(added)[:10]}")
                if removed:
                    print(f"      removed: {sorted(removed)[:10]}")
        if not changed:
            print("  (no table gained or lost rows)")
        print("\nRe-run `python -m data.build` and the test suite before trusting recommendations.")

    print(f"\nPinned to {head['sha'][:12]}.")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--update", action="store_true", help="move the pin to upstream HEAD")
    ap.add_argument("--force", action="store_true", help="re-download files already cached")
    args = ap.parse_args()

    if args.update:
        update_pin()
        return

    sha = pinned_sha()
    meta = json.loads(PIN_FILE.read_text(encoding="utf-8"))
    print(f"Pinned revision {sha[:12]}  ({meta.get('title', '?')})")
    fetch_all(sha, force=args.force)
    print("Cache up to date.")


if __name__ == "__main__":
    main()
