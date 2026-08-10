"""Read a screenshot into candidate entries.

This is a *pre-fill* layer, never an auto-commit one. It groups the text it
finds into cards, hands each card to the resolver, and returns candidates for
you to confirm. When OCR degrades — and it will, the moment the UI changes — the
manual picker is still there and still correct.

The engine is RapidOCR (ONNX, pip-installable, fully offline). If it is not
installed, `available()` returns False and the app keeps working without it.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from functools import lru_cache

from resolve.resolver import Resolution, resolve

# Blessing cards show the Path as a word somewhere on the card; catching it is a
# cheap constraint for the resolver.
PATH_WORDS = [
    "Preservation", "Remembrance", "Nihility", "Abundance", "The Hunt",
    "Destruction", "Elation", "Propagation", "Erudition", "Harmony",
]
RARITY_WORDS = ["Legendary", "Rare", "Common", "Negative"]


@dataclass
class TextBox:
    text: str
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def read_boxes(image_bytes: bytes) -> list[TextBox]:
    """Run OCR and return every text box with its position."""
    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    result, _ = _engine()(np.array(img))
    boxes: list[TextBox] = []
    for item in result or []:
        poly, text, conf = item[0], item[1], item[2]
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        if not text or not text.strip():
            continue
        boxes.append(TextBox(
            text=text.strip(),
            x=min(xs), y=min(ys), w=max(xs) - min(xs), h=max(ys) - min(ys),
        ))
    return boxes


def group_into_cards(boxes: list[TextBox], image_width: float, max_cards: int = 4) -> list[list[TextBox]]:
    """Cluster text boxes into the 2-4 vertical cards of a choice screen.

    DU offers options side by side, so horizontal position separates them. We
    cluster on box centre-x rather than assuming fixed columns, because the
    number of options varies (a Mask screen shows nine, a blessing offer three).
    """
    if not boxes:
        return []
    xs = sorted(b.cx for b in boxes)
    # Split wherever there is a gap wider than a tenth of the image.
    threshold = image_width * 0.10
    cuts = [i for i in range(1, len(xs)) if xs[i] - xs[i - 1] > threshold]
    if len(cuts) + 1 > max_cards:
        # too fragmented — fall back to even columns
        n = max_cards
        edges = [image_width * i / n for i in range(1, n)]
    else:
        edges = [(xs[i] + xs[i - 1]) / 2 for i in cuts]

    def column(b: TextBox) -> int:
        return sum(1 for e in edges if b.cx > e)

    groups: dict[int, list[TextBox]] = {}
    for b in boxes:
        groups.setdefault(column(b), []).append(b)
    return [sorted(g, key=lambda b: b.y) for _, g in sorted(groups.items())]


def _split_card(card: list[TextBox]) -> dict:
    """Separate a card's lines into name, description, and detected constraints.

    The name is the topmost line that is not a Path or rarity label; everything
    below it is description. Truncation markers are preserved — the resolver
    needs to know the text was clipped.
    """
    path = rarity = None
    lines = []
    for b in card:
        t = b.text.strip()
        hit_path = next((p for p in PATH_WORDS if p.lower() in t.lower()), None)
        hit_rarity = next((r for r in RARITY_WORDS if r.lower() == t.lower()), None)
        if hit_path and len(t) <= len(hit_path) + 4:
            path = path or hit_path
            continue
        if hit_rarity:
            rarity = rarity or hit_rarity
            continue
        lines.append(t)

    name = lines[0] if lines else ""
    desc = " ".join(lines[1:]) if len(lines) > 1 else ""
    return {"text": name, "desc_text": desc, "path": path, "rarity": rarity}


def read_offer(image_bytes: bytes, kind: str = "blessing", max_cards: int = 4) -> list[dict]:
    """Full pipeline: screenshot -> per-card candidate lists awaiting confirmation."""
    from PIL import Image

    boxes = read_boxes(image_bytes)
    if not boxes:
        return []
    width = Image.open(io.BytesIO(image_bytes)).width
    cards = group_into_cards(boxes, width, max_cards=max_cards)

    out = []
    for card in cards:
        obs = _split_card(card)
        if not obs["text"] and not obs["desc_text"]:
            continue
        res: Resolution = resolve(
            obs["text"], kind=kind, desc_text=obs["desc_text"],
            path=obs["path"], rarity=obs["rarity"],
        )
        d = res.to_dict()
        d["observed"] = obs
        d["needs_confirmation"] = True   # always: OCR pre-fills, you confirm
        out.append(d)
    return out


def read_inventory(image_bytes: bytes, kinds: tuple[str, ...] = ("blessing", "curio", "weighted_curio")) -> dict:
    """Read an inventory screenshot into confidently-identified item ids.

    Inventory panels are dense grids of short labels rather than a few wide
    cards, so the card-clustering used for offers does not apply — every text
    box is treated as its own candidate label.

    Only unambiguous matches are reported as found. Anything the resolver is
    unsure about goes into `ambiguous` for you to settle, because a wrong entry
    here silently rewrites Path counts and everything downstream of them.
    """
    boxes = read_boxes(image_bytes)
    found: dict[str, list[int]] = {k: [] for k in kinds}
    ambiguous: list[dict] = []
    unmatched: list[str] = []

    for b in boxes:
        text = clean_ocr_text(b.text)
        if len(text) < 4:
            continue
        if any(w.lower() == text.lower() for w in PATH_WORDS + RARITY_WORDS):
            continue

        best_res, best_kind = None, None
        for kind in kinds:
            res = resolve(text, kind=kind, limit=3)
            if not res.candidates:
                continue
            if best_res is None or res.candidates[0].score > best_res.candidates[0].score:
                best_res, best_kind = res, kind

        if best_res is None:
            unmatched.append(text)
            continue

        top = best_res.candidates[0]
        if best_res.ambiguous or top.score < 90:
            ambiguous.append({
                "observed": text,
                "candidates": [c.to_dict() for c in best_res.candidates],
                "note": best_res.note,
            })
        else:
            ids = found.setdefault(top.entry["kind"], [])
            if top.entry["id"] not in ids:
                ids.append(top.entry["id"])

    return {
        "found": {k: v for k, v in found.items() if v},
        "ambiguous": ambiguous,
        "unmatched": unmatched[:20],
        "boxes_read": len(boxes),
    }


def read_options(image_bytes: bytes) -> list[dict]:
    """Read an Occurrence / shop screen into option lines with their live costs.

    Costs in the game data are runtime placeholders, so the numbers that matter
    are the ones rendered on screen. Each line keeps the text exactly as read
    plus any Cosmic Fragment cost parsed out of it.
    """
    from data.options import parse_observed_cost, parse_observed_heat

    boxes = read_boxes(image_bytes)
    lines = []
    for b in sorted(boxes, key=lambda x: (x.y, x.x)):
        text = clean_ocr_text(b.text)
        if len(text) < 3:
            continue
        cost = parse_observed_cost(text)
        heat, heat_max = parse_observed_heat(text)
        lines.append({
            "text": text,
            "cost": cost,
            "heat": heat,
            "heat_max": heat_max,
            "y": round(b.y, 1),
        })
    return lines


def clean_ocr_text(s: str) -> str:
    """Normalise the usual OCR debris before matching."""
    s = re.sub(r"\s+", " ", s or "").strip()
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    return s
