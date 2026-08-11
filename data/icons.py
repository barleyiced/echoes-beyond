"""Fetch the Path and Element icons the UI uses.

Path identity is the whole visual language of Divergent Universe — you read a
blessing card by its Path first — so using the real icons rather than coloured
dots makes the app legible at a glance.

Icons come from Mar-7th/StarRailRes, which is a separate resources repo from the
game-data mirror. They land in `web/static/icons/` and are served locally; the
app degrades to a lettered badge if they are missing, so this step is optional.

    python -m data.icons          # download
    python -m data.icons --check  # report what is present
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

RAW = "https://raw.githubusercontent.com/Mar-7th/StarRailRes/master"
DEST = Path(__file__).resolve().parent.parent / "web" / "static" / "icons"

# The Paths use their own display names, except where StarRailRes keeps the
# older internal naming: The Hunt is "Hunt", Remembrance is filed as "Memory"
# in some builds, and Elation appears as "Joy".
PATH_FILES = {
    "Preservation": "Preservation.png",
    "Remembrance": "Remembrance.png",
    "Nihility": "Nihility.png",
    "Abundance": "Abundance.png",
    "The Hunt": "Hunt.png",
    "Destruction": "Destruction.png",
    "Elation": "Elation.png",
    "Propagation": "Propagation.png",
    "Erudition": "Erudition.png",
    "Harmony": "Harmony.png",
}

# White variants read better on both light and dark backgrounds.
ELEMENT_FILES = {
    "Physical": "PhysicalWhite.png",
    "Fire": "FireWhite.png",
    "Ice": "IceWhite.png",
    "Thunder": "ThunderWhite.png",
    "Wind": "WindWhite.png",
    "Quantum": "QuantumWhite.png",
    "Imaginary": "ImaginaryWhite.png",
}

# Only ever map a Path to art that *is* that Path. StarRailRes ships no
# Propagation icon at all (checked against the full recursive tree), and the
# fallback here used to end "Elation.png" — so every Propagation blessing in the
# app rendered the Elation icon, in the one visual language a player reads a card
# by. A lettered badge on the Path's own colour ring is the honest answer.
FALLBACKS = {
    "Elation": ["Joy.png"],
    "Remembrance": ["Memory.png"],
    "The Hunt": ["TheHunt.png"],
}


# Character avatars, keyed by the same AvatarConfig id the dataset already
# carries. `icon/avatar` is the small round portrait (~35 KB); `icon/character`
# is the half-body art at four times the weight, and `image/character_portrait`
# is 4.6 MB apiece — absurd for a 26px chip. The avatar is the image the game
# itself uses on its team screen, which is also the screen this is read against.
AVATAR_DIRS = ("icon/avatar", "icon/character")


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def _download(url: str, dest: Path) -> bool:
    try:
        r = requests.get(url, timeout=60)
        if r.status_code != 200 or not r.content:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return True
    except requests.RequestException:
        return False


def fetch() -> dict:
    got, missing = [], []

    for path_name, filename in PATH_FILES.items():
        dest = DEST / "path" / f"{_slug(path_name)}.png"
        if dest.exists():
            got.append(path_name)
            continue
        candidates = [filename] + FALLBACKS.get(path_name, [])
        if any(_download(f"{RAW}/icon/path/{c}", dest) for c in candidates):
            got.append(path_name)
        else:
            missing.append(path_name)

    for el, filename in ELEMENT_FILES.items():
        dest = DEST / "element" / f"{_slug(el)}.png"
        if dest.exists():
            got.append(el)
            continue
        if _download(f"{RAW}/icon/element/{filename}", dest):
            got.append(el)
        else:
            missing.append(el)

    return {"downloaded": got, "missing": missing}


def fetch_avatars() -> dict:
    """Character portraits for the team chips and the character search.

    Separate from `fetch()` and tolerant of misses on purpose: this is 84
    requests rather than 17, and a character whose art has not landed in
    StarRailRes yet must not fail the run — the UI falls back to a lettered
    badge exactly as it does for Paths.
    """
    from engine import dataset

    got, missing = [], []
    for ch in dataset.load()["characters"]:
        dest = DEST / "character" / f"{ch['id']}.png"
        if dest.exists():
            got.append(ch["name"])
            continue
        if any(_download(f"{RAW}/{d}/{ch['id']}.png", dest) for d in AVATAR_DIRS):
            got.append(ch["name"])
        else:
            missing.append(ch["name"])
    return {"downloaded": got, "missing": missing}


def check() -> dict:
    present = {
        "path": sorted(p.stem for p in (DEST / "path").glob("*.png")),
        "element": sorted(p.stem for p in (DEST / "element").glob("*.png")),
        "character": sorted(p.stem for p in (DEST / "character").glob("*.png")),
    }
    return present


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.check:
        p = check()
        print(f"paths:      {len(p['path'])}/10  {p['path']}")
        print(f"elements:   {len(p['element'])}/7  {p['element']}")
        print(f"characters: {len(p['character'])}")
        return

    res = fetch()
    print(f"have {len(res['downloaded'])} icons in {DEST}")
    if res["missing"]:
        print(f"missing (UI falls back to lettered badges): {res['missing']}")

    av = fetch_avatars()
    print(f"have {len(av['downloaded'])} character portraits")
    if av["missing"]:
        print(f"no portrait (lettered badge instead): {av['missing']}")


if __name__ == "__main__":
    main()
