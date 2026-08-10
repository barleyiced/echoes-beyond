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
