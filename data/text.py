"""Render game text templates into readable strings.

Descriptions ship as templates with positional placeholders that index into the
row's ParamList::

    "deals DMG equal to <color=#f29e38ff><unbreak>#1[i]%</unbreak></color> of ATK"
    params = [2.8]                    ->  "deals DMG equal to 280% of ATK"

Format specs follow the usual HoYo convention: ``i`` is an integer, ``fN`` is N
decimal places, and a trailing ``%`` means the stored value is a ratio that must
be multiplied by 100 before display.
"""

from __future__ import annotations

import re
import unicodedata

# #1[i]  #2[i]%  #1[f1]  #3[f2]%
PARAM_RE = re.compile(r"#(\d+)\[([if])(\d*)\](%?)")
# #2 — same ParamList, no format spec. Opt-in per caller (`render(..., bare=True)`)
# because event option texts also contain bare #N and theirs are runtime figures
# that do *not* index the row's ParamList; substituting there invents numbers.
BARE_PARAM_RE = re.compile(r"#(\d+)(?!\[)")
# #{blackboard:MazeBuffParam_1}[i] — resolved at runtime by the game, not by us.
# `gblackboard` is the global-scope spelling and the format spec is optional:
# one curio ships `#{gblackboard:FruitBuffCount}[i]` and another form would have
# gone straight to the screen as raw template text.
BLACKBOARD_RE = re.compile(r"#\{g?blackboard:[^}]+\}(?:\[[if]\d*\])?%?")
TAG_RE = re.compile(r"</?(color|unbreak|i|u|b|size|align)[^>]*>", re.I)
LINEBREAK_RE = re.compile(r"\\n|\r\n|\r")


def format_value(value: float, kind: str, decimals: str, percent: str) -> str:
    if percent:
        value = value * 100
    if kind == "i":
        return f"{round(value):g}" + ("%" if percent else "")
    nd = int(decimals) if decimals else 1
    return f"{value:.{nd}f}" + ("%" if percent else "")


def render(template: str, params: list[float] | None = None, *,
           strip_markup: bool = True, bare: bool = False) -> str:
    """Substitute `params` into `template` and optionally drop the rich-text tags.

    `bare` additionally substitutes spec-less `#2` references — see BARE_PARAM_RE
    for why that is off by default.
    """
    if not template:
        return ""
    params = params or []

    def sub(m: re.Match) -> str:
        idx = int(m.group(1)) - 1
        if idx < 0 or idx >= len(params) or params[idx] is None:
            return m.group(0)
        return format_value(float(params[idx]), m.group(2), m.group(3), m.group(4))

    def sub_bare(m: re.Match) -> str:
        idx = int(m.group(1)) - 1
        if idx < 0 or idx >= len(params) or params[idx] is None:
            return m.group(0)
        return f"{float(params[idx]):g}"

    out = PARAM_RE.sub(sub, template)
    if bare:
        out = BARE_PARAM_RE.sub(sub_bare, out)
    out = BLACKBOARD_RE.sub("(varies)", out)
    if strip_markup:
        out = TAG_RE.sub("", out)
    out = LINEBREAK_RE.sub(" ", out)
    out = re.sub(r"\{SPACE\}", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def plain(template: str) -> str:
    """Strip markup and placeholders without substituting.

    Used for search text and for the Occurrence options, whose numbers are
    runtime figures that do not index the row's ParamList — see NOTES.md. "N"
    stands in for a number nobody can know from the files, and inventing one
    would be worse than admitting it.

    Two things the marker has to get right, both of which reached real cards:

    * **Keep the unit.** `PARAM_RE` captures a trailing `%` in group 4, and
      replacing the whole match dropped it: "A #1[i]% chance" rendered as
      "A N chance", which does not say it is a percentage at all. 114 of the
      124 option descriptions carrying a placeholder were affected.
    * **Mark the spec-less ones too.** A bare `#2` matches no PARAM_RE, so 640
      option descriptions shipped a literal "Consumes #2 Cosmic Fragments",
      which reads as a bug rather than as a placeholder. This is not the
      `render(bare=True)` decision reversed — that one is about *substituting a
      value*, which would invent a number. Writing "N" invents nothing.
    """
    out = PARAM_RE.sub(lambda m: "N" + m.group(4), template or "")
    out = BARE_PARAM_RE.sub("N", out)
    out = BLACKBOARD_RE.sub("N", out)
    out = TAG_RE.sub("", out)
    out = LINEBREAK_RE.sub(" ", out)
    out = re.sub(r"\{SPACE\}", " ", out)
    return re.sub(r"\s+", " ", out).strip()


# --------------------------------------------------------------------------
# search folding
# --------------------------------------------------------------------------

# Letters that carry no combining mark, so NFKD leaves them exactly as they are.
# Decomposing is enough for "Désastre" and "Disperării"; it does nothing for a
# stroked or ligatured letter, and "Døden" split on a naive [^A-Za-z] becomes
# "D" and "den". Only ø appears in the pinned data. The rest are here so the next
# patch's name does not need a code change, and `verify()` fails the build on an
# unmapped one rather than letting it silently become unsearchable.
FOLD_MAP = {
    "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "ß": "ss", "ð": "d", "Ð": "D", "þ": "th", "Þ": "TH",
    "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ı": "i", "ħ": "h", "ŧ": "t",
}

_FOLD_RE = re.compile(r"[^\w]+", re.UNICODE)


def fold(text: str) -> str:
    """ASCII-fold text for searching, so what you can type finds what is stored.

    Applied to the indexed text *and* to the query, which is the only way typing
    "doden" reaches "Sygdommen til Døden" and "lexperience interieure" reaches
    "L'Expérience Intérieure". Folding one side alone just moves the mismatch.
    """
    out = "".join(FOLD_MAP.get(ch, ch) for ch in text)
    out = unicodedata.normalize("NFKD", out)
    return "".join(ch for ch in out if not unicodedata.combining(ch))


_JOINABLE_RE = re.compile(r"[\w]+(?:['’-][\w]+)+", re.UNICODE)


def index_text(text: str) -> str:
    """Folded text for the index, plus the run-together form of joined words.

    A reader types a name the way it looks, and an apostrophe or a hyphen is the
    one thing they drop. "L'Expérience Intérieure" splits to `l` and `experience`,
    so somebody typing "lexperience interieure" matches neither. Indexing the
    joined form as well means both spellings land, where picking one side of the
    split would always strand the other.
    """
    folded = fold(text)
    extra = [m.group(0) for m in _JOINABLE_RE.finditer(folded)]
    joined = [re.sub(r"['’-]", "", e) for e in extra]
    joined = [j for j in joined if j and j not in extra]
    return " ".join([folded] + joined)


def search_tokens(text: str) -> list[str]:
    """Fold, then split on anything that is not a word character.

    Splitting matters as much as folding. The old code deleted the separator
    instead, which turned "Ever-Peaceful" into one token the index never holds,
    so every hyphenated name was unreachable even when pasted whole.
    """
    return [t for t in _FOLD_RE.split(fold(text)) if t]
