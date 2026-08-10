"""Match an entry's mechanic tags against the team's."""

from __future__ import annotations

from data.tags import PATH_AFFINITY
from engine.state import RunState


def team_tags(run: RunState) -> dict[str, float]:
    """Weighted tag profile for what this run wants.

    Two sources feed it:

    1. The team. Each character contributes the tags their Path implies, so a
       tag two characters both want outranks one only a single character cares
       about — "my team is built around this" beats "one character likes this".

    2. The blessings already held. Each Path in a DU theme has one signature
       mechanic (Murmur, Blazar, Harmonize, ...), and once you hold several
       blessings of a Path, further pieces of that mechanic are worth more than
       raw stats. Without this the engine cannot see that a build is forming.
    """
    profile: dict[str, float] = {}
    for ch in run.team:
        for tag in PATH_AFFINITY.get(ch.path, []):
            profile[tag] = profile.get(tag, 0) + 1.0

    # Mechanics the run has already invested in.
    from engine import dataset
    mech_weight: dict[str, float] = {}
    for bid in run.owned_blessings:
        b = dataset.get("blessing", bid)
        if not b:
            continue
        for tag in b.get("tags", []):
            if tag.startswith("mech:"):
                mech_weight[tag] = mech_weight.get(tag, 0) + 1.0
    for tag, n in mech_weight.items():
        # Two pieces of a mechanic is a build; one is a coincidence.
        profile[tag] = profile.get(tag, 0) + min(2.0, n)

    if not profile:
        return {}
    peak = max(profile.values())
    return {t: v / peak for t, v in profile.items()}


def synergy_score(entry_tags: list[str], profile: dict[str, float],
                  generic_good: list[str]) -> tuple[float, list[str]]:
    """Return 0..1 synergy and the tags that produced it.

    Generic-good tags contribute at a reduced rate so that a blessing which
    merely says "increases DMG" cannot outscore one that plugs into the team's
    actual engine.
    """
    if not entry_tags:
        return 0.0, []

    matched, score = [], 0.0
    for tag in entry_tags:
        w = profile.get(tag)
        if w:
            score += w
            matched.append(tag)
        elif tag in generic_good:
            score += 0.25
            matched.append(f"{tag} (generic)")

    # Normalise against a realistic best case rather than the theoretical one, so
    # a strong-but-not-perfect match still lands high.
    return min(1.0, score / 3.0), matched


def element_fit(entry_elements: list[str], run: RunState) -> float:
    """1.0 when an element-specific entry matches the team, 0.0 when it does not."""
    if not entry_elements:
        return 1.0                      # element-agnostic
    team = run.team_elements()
    if not team:
        return 0.5                      # unknown team, do not punish
    return 1.0 if set(entry_elements) & team else 0.0


def gate_fit(entry: dict, run: RunState) -> tuple[float, str]:
    """Hard gate for weighted curios, which only work on matching characters.

    A weighted curio your team cannot trigger is worth approximately nothing no
    matter how strong the numbers look, so this returns 0 and says why.
    """
    gate_paths = entry.get("gate_paths") or []
    gate_elements = entry.get("gate_elements") or []
    if not gate_paths and not gate_elements:
        return 1.0, "no restriction"

    team_paths, team_elements = run.team_paths(), run.team_elements()
    if not team_paths and not team_elements:
        return 0.5, "team unknown"

    hits = len(set(gate_paths) & team_paths) + len(set(gate_elements) & team_elements)
    if hits == 0:
        want = ", ".join(gate_paths + gate_elements)
        return 0.0, f"no character matches ({want})"

    # More matching characters means the effect applies more widely.
    matched = sorted((set(gate_paths) & team_paths) | (set(gate_elements) & team_elements))
    return min(1.0, 0.55 + 0.25 * hits), "matches " + ", ".join(matched)
