"""A readable picture of what the run is actually holding.

The run state stores ids. That is right for the engine and useless to a person:
"blessing 617042" says nothing, and a flat list of forty names says only slightly
more. What a player needs to see when they open the inventory is the shape of the
build — which Paths the blessings are stacked on, which Equations that has already
switched on, which of the curios are actually dead weight.

So every group here answers a question rather than just listing rows:

* **Blessings** are grouped by Path in the game's own Path order, each Path
  carrying what it is building toward. That is the same view the Paths table
  gives, but with the actual blessings under it — and the same order, so the two
  screens can be read against each other.
* **Equations** are split into live and still-short, because an Equation you hold
  but have not met the Path requirement for is doing nothing yet.
* **Weighted curios** are checked against the team, since one your team cannot
  trigger is worth nothing and should look different from one that works.
* **Negative curios** are called out — they are held involuntarily and are easy
  to forget you are carrying.

Nothing here scores or recommends. It reports.
"""

from __future__ import annotations

from engine import dataset, equations, rating, scoring
from engine.state import RunState
from engine.synergy import gate_fit, synergy_score, team_tags

# Owned list on RunState -> the dataset kind it holds.
FIELD_KIND = {
    "owned_blessings": "blessing",
    "owned_curios": "curio",
    "owned_weighted": "weighted_curio",
    "owned_equations": "equation",
    "owned_miracles": "miracle",
}


def _score_weighted(entry: dict, run: RunState, profile, generic) -> dict:
    """What one Weighted Curio is worth to *this* run.

    Nothing here reads whether the run holds it — the score is the gate against
    the team and what the effect does for the build, both of which are true of a
    curio you are only looking at. That is what lets the same number rank the
    17-entry pool you are browsing and the handful you have ticked, and it is
    why the pool order does not move under the cursor as you tick.
    """
    fit, why = gate_fit(entry, run)
    syn, matched = synergy_score(entry.get("tags", []), profile, generic)
    score = fit * (0.6 + 0.4 * syn)
    reasons = [why]
    if fit == 0.0:
        reasons.append("does nothing in a socket, because the gate never opens for this team")
    elif matched:
        reasons.append("fits the build: " + ", ".join(matched[:3]))
    elif not entry.get("tags"):
        reasons.append("no machine-readable effect text, so this is gate-only judgement")
    return {
        "id": entry["id"], "name": entry["name"], "desc": entry.get("desc", ""),
        "gate_paths": entry.get("gate_paths") or [],
        "gate_elements": entry.get("gate_elements") or [],
        "score": round(score, 2), "gate_fit": round(fit, 2),
        "reasons": reasons, "equipped": run.is_equipped(entry["id"]),
    }


def weighted_plan(run: RunState) -> dict:
    """What is in the sockets, and whether anything beats it.

    **Only socketing counts.** Reported from play: by late run you own most of
    the theme, so "which ones do you have" is nearly the whole list and carries
    no information — the decision, and the only thing that touches the run, is
    which `weighted_slots` of them are in the sockets. So the run tracks the
    socketed set and this reports on it. Two things decide whether a socket is
    well spent:

    * **The gate is a hard filter, not a preference.** One no character in your
      team matches does literally nothing in the socket, however good the line
      reads. `gate_fit` already answers this and returns 0 with the reason.
    * **After the gate, what it does for the team.** This only became possible to
      answer once the effect text existed — all 17 built with an empty `desc` and
      no tags until the MazeBuff join was fixed, so before that a ranking here
      could only have compared gates.

    `ranked` is what is socketed. `pool` is the whole 17 scored the same way, for
    the browse-and-tick list. `best_available` is the top of that pool and is
    **a suggestion, never an instruction**: the catalog cannot know which ones
    this run has been given, so nothing here writes a socket and the note that
    names one says "if the equip screen is offering it". Asserting possession is
    the held-vs-catalog line the rating and the Equation credit each crossed once.
    """
    profile = team_tags(run)
    generic = scoring.weights()["generic_good_tags"]

    pool = [_score_weighted(e, run, profile, generic)
            for e in dataset.load()["weighted_curios"]]
    # Ties broken by name so the list a player is reading down is stable between
    # renders — most of a 17-entry pool can share a score.
    pool.sort(key=lambda w: (-w["score"], w["name"]))

    held_ids = set(run.owned_weighted)
    for w in pool:
        w["held"] = w["id"] in held_ids

    slots = max(0, run.weighted_slots)
    live = set(run.live_weighted())
    ranked = [dict(w) for w in pool if w["id"] in live]

    # **The suggestion is sized by the free sockets, not by the socket count.**
    # Reported by the user: with one socket and it filled, a second row still
    # carried the highlight, which reads as "tick this too" on a screen where
    # there is nowhere to put it. So `best_available` is at most `free` long — 3
    # sockets with 2 filled highlights exactly one, and a full set highlights
    # nothing. Whether something already in beats it is a *note*, not a
    # highlight: a swap costs a socket you have to empty first.
    free = max(0, slots - len(ranked))
    candidates = [w for w in pool if w["score"] > 0 and w["id"] not in live]
    best_available = candidates[:free]

    notes = []
    if not ranked:
        notes.append(
            "Nothing is socketed. Tick whatever the equip screen has in yours. The list "
            "is ranked best first for this team.")
        if best_available:
            notes.append(
                "Best in the theme for this team: "
                + ", ".join(f"{w['name']} ({w['score']:.2f})" for w in best_available) + ".")
    elif free > 0:
        notes.append(
            f"{len(ranked)} of your {slots} sockets filled, so {free} is doing nothing."
            + (f" Best you could put in: {best_available[0]['name']} "
               f"({best_available[0]['score']:.2f})." if best_available else ""))
    elif len(ranked) > slots:
        notes.append(
            f"You ticked {len(ranked)} with only {slots} socket(s). Untick the ones you have "
            f"not actually equipped, or raise the socket count.")

    dead = [w for w in ranked if w["gate_fit"] == 0.0]
    if dead:
        notes.append(
            f"{len(dead)} socketed curio(s) no character in your team can trigger, so "
            f"they are doing literally nothing: {', '.join(w['name'] for w in dead[:3])}.")

    # With every socket full the only move left is a swap, and that is a note
    # rather than a highlight — it costs a socket you have to empty first, so it
    # is a sentence naming both sides of the trade, not a row saying "tick me".
    # Its absence is a verdict too: a full set of sockets with nothing said about
    # them reads as "the planner did not run", the same silence
    # `store.plan_shelf` used to produce.
    if ranked and free == 0 and candidates:
        worst = min(ranked, key=lambda w: w["score"])
        top = candidates[0]
        if top["score"] > worst["score"] + 0.05:
            notes.append(
                f"Every socket is full. {top['name']} scores {top['score']:.2f} against the "
                f"{worst['score']:.2f} of {worst['name']} in your socket. Worth swapping "
                f"if the equip screen is offering it.")
        elif not dead:
            notes.append(
                "Every socket is filled and nothing else in the theme scores meaningfully "
                "higher for this team. This is as good as it gets without a team change.")

    # An inventory scan writes what you *hold*, which is the one way the two
    # lists can still come apart. A bigger count with nothing said about it is
    # the confusion this surface was rebuilt to remove.
    unsocketed = held_ids - live
    if unsocketed:
        notes.append(
            f"You hold {len(unsocketed)} more without socketing them. Only socketed ones "
            f"count, so tick them if they are in fact equipped.")

    return {
        "ranked": ranked, "best_available": [w["id"] for w in best_available], "pool": pool,
        "slots": slots, "equipped": sorted(live), "notes": notes,
    }


def _blessing(run: RunState, entry_id: int) -> dict:
    """An owned Blessing, described at the level it is actually at.

    An enhanced Blessing has different numbers — "by 5" becomes "by 10" — so
    showing the level-1 text for something you have already maxed is showing you
    the wrong build.
    """
    out = _entry("blessing", entry_id)
    entry = dataset.get("blessing", entry_id)
    enhanced = run.is_enhanced(entry_id)
    out["enhanced"] = enhanced
    out["enhanceable"] = bool(entry and entry.get("max_level", 1) > 1)
    if entry:
        level = run.blessing_level(entry_id)
        out["level"] = level
        out["max_level"] = entry.get("max_level", 1)
        levels = entry.get("levels") or []
        if 0 < level <= len(levels):
            out["desc"] = levels[level - 1].get("desc") or out["desc"]
    return out


def _entry(kind: str, entry_id: int) -> dict:
    """Resolve one owned id, never dropping it if the lookup fails.

    An unresolvable id is shown as unknown rather than silently vanishing: it
    means the dataset was rebuilt under the run (ids move between patches), and
    quietly removing items would rewrite Path counts without telling anyone.
    """
    e = dataset.get(kind, entry_id) or {}
    return {
        "id": entry_id,
        "kind": kind,
        "name": e.get("name") or f"unknown {kind} ({entry_id})",
        "path": e.get("path", ""),
        "rarity": e.get("rarity", ""),
        "desc": e.get("desc", "") or e.get("effect", ""),
        "is_negative": bool(e.get("is_negative")),
        "unknown": not e,
    }


def summary(run: RunState) -> dict:
    """Everything the run holds, resolved and organised."""
    counts = run.path_counts()
    picks = run.picks_remaining()

    blessings = [_blessing(run, i) for i in run.owned_blessings]
    curios = [_entry("curio", i) for i in run.owned_curios]
    weighted = [_entry("weighted_curio", i) for i in run.owned_weighted]
    miracles = [_entry("miracle", i) for i in run.owned_miracles]

    # --- blessings grouped by Path ----------------------------------------
    targets = {p["path"]: p["next_target"] for p in equations.path_summary(counts, picks)}
    by_path: dict[str, list[dict]] = {}
    for b in blessings:
        by_path.setdefault(b.get("path") or "No Path", []).append(b)

    paths = []
    for path, items in by_path.items():
        target = targets.get(path)
        paths.append({
            "path": path,
            "count": len(items),
            "committed": len(items) >= 3,
            "next_target": target,
            "note": (f"{target['distance']} more for {target['name']}" if target
                     else "not on any Equation you can still finish"),
            "entries": sorted(items, key=lambda e: e["name"]),
        })
    # Game order, not count order. Sorting by how many you hold re-arranges the
    # blocks every time you take a blessing, so the Path you were reading moves
    # under you and you have to re-find it by label. A fixed order means the
    # layout is memorable and "how committed am I" is read off the counts, which
    # is what the count pill and the committed border are already for.
    paths.sort(key=lambda p: dataset.path_rank(p["path"]))

    # --- equations, live or still short -----------------------------------
    in_theme = set(dataset.paths_in_theme())
    eqs = []
    for eid in run.owned_equations:
        e = dataset.get("equation", eid)
        if e is None:
            eqs.append({**_entry("equation", eid), "active": False, "distance": None})
            continue
        st = equations.status_for(e, counts, in_theme)
        eqs.append({
            "id": e["id"], "kind": "equation", "name": e["name"],
            "rarity": e["rarity"],
            "rarity_label": "Boundary" if e["is_boundary"] else e["rarity"],
            "desc": e["desc"], "requires": e["requires"],
            "active": st.active, "distance": st.distance, "missing": st.missing,
            "reachable": st.reachable, "blocked_paths": st.blocked_paths,
            "unknown": False,
        })
    eqs.sort(key=lambda e: (not e.get("active"), e.get("distance") or 0, e["name"]))

    # --- weighted curios against the team ---------------------------------
    live = set(run.live_weighted())
    for w in weighted:
        entry = dataset.get("weighted_curio", w["id"])
        if entry is None:
            continue
        fit, why = gate_fit(entry, run)
        w["gate_paths"] = entry.get("gate_paths") or []
        w["gate_elements"] = entry.get("gate_elements") or []
        w["gate_fit"] = round(fit, 2)
        w["gate_note"] = why
        w["dead"] = fit == 0.0
        w["equipped"] = w["id"] in live

    enhanced = [b for b in blessings if b.get("enhanced")]
    upgradable = [b for b in blessings if b.get("enhanceable") and not b.get("enhanced")]
    negatives = [c for c in curios if c["is_negative"]]
    live_eq = [e for e in eqs if e.get("active")]
    # Only a socketed one is doing anything, so only a socketed one is worth
    # warning about — an untriggerable curio sitting outside the sockets costs
    # the run nothing.
    dead_weighted = [w for w in weighted if w.get("dead") and w.get("equipped")]
    unknown = [e for e in blessings + curios + weighted + miracles + eqs if e.get("unknown")]

    notes = []
    if run.owned_blessings and not live_eq and run.owned_equations:
        notes.append(
            f"You hold {len(run.owned_equations)} Equation(s) and none of them are live yet. "
            f"You do not meet their Path requirements, so they do nothing right now."
        )
    if len(paths) >= 4 and not any(p["committed"] for p in paths):
        notes.append(
            f"Your blessings are spread across {len(paths)} Paths with none at 3 or more. "
            f"Equations need concentration, and there are about {picks} picks left."
        )
    if negatives:
        notes.append(
            f"{len(negatives)} negative curio(s) in the bag: "
            f"{', '.join(c['name'] for c in negatives[:3])}."
        )
    if dead_weighted:
        notes.append(
            f"{len(dead_weighted)} socketed weighted curio(s) your current team cannot "
            f"trigger: {', '.join(w['name'] for w in dead_weighted[:3])}."
        )
    if unknown:
        notes.append(
            f"{len(unknown)} tracked item(s) no longer resolve against the dataset. That "
            f"usually means the game data was rebuilt mid-run. Add them again by name."
        )

    return {
        "totals": {
            "blessings": len(blessings),
            "enhanced": len(enhanced),
            "upgradable": len(upgradable),
            "equations": len(eqs),
            "equations_active": len(live_eq),
            "curios": len(curios),
            # Socketed, not held. The tile used to count everything recorded,
            # which is how a run with 2 of 2 sockets filled showed a 10.
            "weighted": len([w for w in weighted if w.get("equipped")]),
            "weighted_held": len(weighted),
            "weighted_slots": max(0, run.weighted_slots),
            "miracles": len(miracles),
            "paths_used": len(paths),
        },
        "paths": paths,
        # The socket verdict, without the 17-entry `pool` — this payload is
        # rebuilt on nearly every click on that tab and the browse list is a
        # different screen.
        "weighted_plan": {k: v for k, v in weighted_plan(run).items() if k != "pool"},
        "equations": eqs,
        "curios": sorted(curios, key=lambda c: (not c["is_negative"], c["name"])),
        # Socketed first — those are the ones doing something.
        "weighted": sorted(weighted, key=lambda w: (not w.get("equipped"), w["name"])),
        "miracles": miracles,
        "picks_remaining": picks,
        "notes": notes,
        # How the run is going, graded against a reference run at the same
        # Domain. Report only — see engine/rating.py. An empty bag has nothing
        # to grade, and a 0/100 on Domain 1 would be noise rather than news.
        "rating": rating.rate(run) if run.owned_blessings or run.owned_curios
                  or run.owned_equations or run.owned_weighted else None,
    }
