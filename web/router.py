"""Transport-agnostic dispatch for every API route.

`web/app.py` is a FastAPI shim over this; the browser build (see WEB-PLAN.md)
will be a postMessage shim over the same thing. One dispatch layer, two shims —
because if each transport owned its own route bodies they would drift, and the
drift would be invisible until a friend got a different verdict than you did for
the same run.

Nothing here imports FastAPI, pydantic, or anything else transport-shaped. The
contract is plain dicts in, plain dicts out, and `ApiError` for the failure
cases the caller is expected to show the user.

Route bodies read their inputs out of `body` rather than off a validated model,
so the defaults live in exactly one place: `RunState`'s own dataclass fields, via
`RunState.from_dict`. `tests/test_router.py` pins that the pydantic layer agrees
with it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl

from engine import (dataset, economy, equations, explain, inventory, masks,
                    miracles, owned, scoring, store, waypoint)
from engine import state as run_state
from engine.state import RunState


class ApiError(Exception):
    """A failure the caller should surface, with an HTTP-shaped status.

    The status is carried rather than raised as an HTTPException so this module
    stays transport-agnostic; each shim maps it to its own idiom (an
    HTTPException for FastAPI, a rejected promise for the worker).
    """

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ------------------------------------------------------------------ helpers

def _run(body: dict, key: str | None = "run") -> RunState:
    """The RunState from a body, whether nested under "run" or sent bare.

    Both shapes exist in the current API and the frontend depends on which is
    which, so this preserves rather than unifies them.
    """
    d = body if key is None else (body.get(key) or {})
    return RunState.from_dict(d)


def _int(value: Any, default: int | None = None) -> int | None:
    """A number that may have arrived as text from a query string.

    FastAPI coerced these off the signature; `dispatch` deliberately does not
    guess, so the handlers that want a number say so here.
    """
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(400, f"expected a number, got {value!r}")


def _entry(kind: str, entry_id: Any) -> dict:
    e = dataset.get(kind, entry_id)
    if e is None:
        raise ApiError(404, f"unknown {kind} id {entry_id}")
    return e


# ------------------------------------------------------------------- routes
# Handlers are named for their path. Each takes the request body as a plain
# dict and returns JSON-serialisable data.

def meta(body: dict) -> dict:
    ds = dataset.load()
    return {
        "meta": ds["meta"],
        "paths": ds["paths"],
        "masks": [
            {k: m[k] for k in ("id", "name", "tagline", "effect", "wishpower", "flavour")}
            for m in ds["masks"]
        ],
        "characters": ds["characters"],
        "run_lengths": ds["run_lengths"],
        "counts": {k: len(ds[k]) for k in
                   ("blessings", "equations", "curios", "weighted_curios", "masks")},
    }


def _fold_repeats(entries: list[dict]) -> list[dict]:
    """Collapse Occurrence lines that are identical in everything we can read.

    An escalating gamble is one row per stage: group 2247 holds five separate
    "Gather more of the deep data" ids, and 2253 (Dolos Dice) the same shape.
    They *are* different game states — stage five has worse odds than stage one —
    but the only thing that differs is a runtime number the files do not carry,
    so the catalog cannot tell them apart and neither can the reader. Listing
    five indistinguishable rows makes a working search look broken and asks for
    a choice that does not exist.

    Keyed on `(group, name, desc)` — the group has to be in the key, or the same
    line from two different Occurrences would merge and hide a real distinction:
    they have different siblings. 59 sets, covering 177 of 1059 options.

    Folded rows keep the first id and say what was folded, because a row that
    quietly stands for five is exactly the kind of thing invariant 4 exists to
    stop.
    """
    seen: dict[tuple, dict] = {}
    out: list[dict] = []
    for e in entries:
        if e.get("kind") != "option":
            out.append(e)
            continue
        key = (e.get("group"), e.get("name"), e.get("desc"))
        first = seen.get(key)
        if first is None:
            seen[key] = e
            out.append(e)
            continue
        first["repeats"] = first.get("repeats", 1) + 1
        first["repeat_ids"] = first.get("repeat_ids", [first["id"]]) + [e["id"]]
        first["repeat_note"] = (
            f"appears {first['repeats']}x in this Occurrence, and the stages differ "
            f"only in numbers the game files do not carry")
    return out


def search(body: dict) -> dict:
    return {"results": _fold_repeats([
        {
            "id": e["id"], "kind": e["kind"], "name": e.get("name", ""),
            "path": e.get("path", ""), "rarity": e.get("rarity", ""),
            "desc": e.get("desc", "") or e.get("effect", ""),
            "group": e.get("group"),
        }
        for e in dataset.search(body.get("q", ""), kind=body.get("kind") or None,
                                limit=_int(body.get("limit"), 15))
    ])}


def resolve_one(body: dict) -> dict:
    # Imported lazily: resolve/resolver.py needs rapidfuzz, which has no Pyodide
    # build (WEB-PLAN.md spike 0a). Keeping the import inside the handler means
    # importing this module in the browser does not drag it in, and only the
    # resolver routes -- which the UI never calls -- would fail there.
    try:
        from resolve.resolver import resolve
    except ImportError:
        raise ApiError(503, "the resolver is not available on this build")
    return resolve(
        body.get("text", ""), kind=body.get("kind"),
        desc_text=body.get("desc_text", ""),
        path=body.get("path"), rarity=body.get("rarity"),
    ).to_dict()


def resolve_batch(body: dict) -> dict:
    try:
        from resolve.resolver import resolve_many
    except ImportError:
        raise ApiError(503, "the resolver is not available on this build")
    obs = body.get("observations", [])
    return {"results": [r.to_dict() for r in resolve_many(obs, kind=body.get("kind"))]}


def rank(body: dict) -> dict:
    run = _run(body)
    kind = body.get("kind", "blessing")
    entries = [_entry(kind, i) for i in body.get("ids", [])]
    if not entries:
        raise ApiError(400, "no options supplied")
    return scoring.rank(entries, run)


def equations_status(body: dict) -> dict:
    run = _run(body, key=None)
    counts = run.path_counts()
    picks = run.picks_remaining()
    return {
        "path_counts": counts,
        "picks_remaining": picks,
        "paths": equations.path_summary(counts, picks),
        "active": [s.to_dict() for s in equations.all_status(counts) if s.active],
        "reachable": [s.to_dict() for s in
                      equations.reachable(counts, picks, include_active=False)[:12]],
        "unreachable_paths": [
            p["name"] for p in dataset.load()["paths"] if not p["in_theme"]
        ],
    }


def run_save(body: dict) -> dict:
    """Persist the run.

    `force_snapshot` is a transport-level flag rather than part of the run: it
    bypasses history coalescing for a save that is a deliberate jump rather than
    part of an edit burst. Importing a run is one, the same way restoring is.
    `RunState.from_dict` ignores the key, so it never reaches the state.
    """
    forced = bool(body.get("force_snapshot"))
    return {"saved": str(_run(body, key=None).save(force_snapshot=forced))}


def run_load(body: dict) -> dict:
    return RunState.load().to_dict()


def run_history(body: dict) -> dict:
    """Recent saved states, newest first. See engine/state.py — every save keeps
    what it replaced, so a reset or a mis-click can be walked back."""
    return {"snapshots": run_state.history()}


def run_restore(body: dict) -> dict:
    try:
        return run_state.restore(body.get("file", "")).to_dict()
    except FileNotFoundError:
        raise ApiError(404, "that snapshot is gone")
    except ValueError as e:
        raise ApiError(400, str(e))


def masks_rank(body: dict) -> dict:
    """Rank the Masks the run offered at the start."""
    mask_ids = body.get("mask_ids", [])
    if not mask_ids:
        raise ApiError(400, "no masks supplied")
    return masks.rank(mask_ids, _run(body))


def miracles_rank(body: dict) -> dict:
    """Rank the Wishpower Miracles a level-up is offering, reshuffle included."""
    miracle_ids = body.get("miracle_ids", [])
    if not miracle_ids:
        raise ApiError(400, "no miracles supplied")
    run = _run(body)
    resets = body.get("resets_remaining")
    if resets is None:
        resets = run.miracle_resets
    return miracles.rank(miracle_ids, run, resets)


def deck_targets(body: dict) -> dict:
    """Which Domain to designate — the choice a Miracle hands you afterwards.

    With no cards it answers by Domain type, which needs no data entry; with your
    actual draw pile it accounts for level and beacons too.
    """
    run = _run(body)
    intent = body.get("intent", "sacrifice")
    restricted = body.get("restricted_to", [])
    action = None
    miracle_id = body.get("miracle_id")
    if miracle_id is not None:
        m = dataset.get("miracle", miracle_id)
        spec = miracles.targeting(m) if m else None
        if spec:
            intent, action = spec["intent"], spec["action"]
            restricted = restricted or spec["restricted_to"]
    cards = body.get("cards", [])
    if not cards:
        return waypoint.target_guidance(run, intent, restricted or None, action)
    return waypoint.rank_targets(cards, run, intent, restricted or None)


def miracles_pool(body: dict) -> dict:
    """Every Miracle the chosen Mask can be offered.

    The pool is browsable rather than only searchable because 136 of the 286
    Miracles share three names between them — you cannot type your way to the
    right row, you have to recognise the effect.
    """
    run = _run(body, key=None)
    entries = miracles.pool(run)
    mask = run.mask()
    return {
        "mask": {"id": mask["id"], "name": mask["name"]} if mask else None,
        "count": len(entries),
        "miracles": [
            {"id": m["id"], "name": m["name"], "rarity": m["rarity"],
             "effect": m["effect"], "universal": m.get("universal", False)}
            for m in entries
        ],
    }


def owned_summary(body: dict) -> dict:
    """What the run is holding, resolved and grouped — see engine/owned.py."""
    return owned.summary(_run(body, key=None))


def waypoint_doors(body: dict) -> dict:
    """Which door to take, given where in the run you are — redrawing included."""
    return waypoint.rank_doors(body.get("doors", []), _run(body),
                               body.get("redraws_remaining"))


def workbench(body: dict) -> dict:
    """What to spend Heat on — including spending none."""
    run = _run(body)
    candidate_ids = body.get("candidate_ids")
    verdicts = economy.decide_workbench(run, candidate_ids)
    return {
        "verdicts": [v.to_dict() for v in verdicts],
        # Enhancing costs 1/2/3 Heat by rarity, so the useful answer is the best
        # affordable *combination*, not a ranked list read top-down.
        "plan": economy.plan_workbench(run, candidate_ids),
        "heat": run.heat, "heat_max": run.heat_max,
        "note": ("The Workbench resets your Heat when you leave, so you lose anything "
                 "you do not spend here."),
        "endgame_advice": economy.spend_everything_advice(run),
    }


def offer(body: dict) -> dict:
    """Rank event / shop options against buying nothing."""
    run = _run(body)
    offered_kind = body.get("offered_kind", "blessing")
    options = [o for o in (dataset.get("option", i)
                           for i in body.get("option_ids", [])) if o]
    entries = [e for e in (dataset.get(offered_kind, i)
                           for i in body.get("offered_entry_ids", [])) if e]
    costs = {int(k): v for k, v in (body.get("costs") or {}).items()}
    verdicts = economy.decide_offer(options, run, costs=costs,
                                    refresh_cost=body.get("refresh_cost", 0),
                                    offered_entries=entries)
    return {
        "verdicts": [v.to_dict() for v in verdicts],
        "fragments": run.fragments,
        "fragment_scarcity": round(economy.fragment_scarcity(run), 2),
        "endgame_advice": economy.spend_everything_advice(run),
    }


def store_shelf(body: dict) -> dict:
    """Rank a store shelf against walking out — which is the default answer."""
    run = _run(body)
    kind = body.get("kind", "curio")
    if kind not in ("curio", "blessing"):
        raise ApiError(400, f"unknown shelf kind {kind!r}")

    items = []
    for item in body.get("items", []):
        entry = dataset.get(kind, item.get("id"))
        if entry is None:
            raise ApiError(404, f"unknown {kind} {item.get('id')}")
        # No price table is datamined, so the printed number wins; the rarity
        # default is only a prefill for a card whose price was not typed in.
        cost = item.get("cost") or run.store_price(entry)
        items.append({"id": item.get("id"), "cost": cost})

    shelf = store.Shelf(
        items=items,
        refresh_cost=body.get("refresh_cost", 0),
        refreshes_left=body.get("refreshes_left"),
        kind=kind,
    )
    return store.decide_store(shelf, run)


def option_set(body: dict) -> dict:
    """The other options that appear on the same Occurrence as this one.

    Grouped by id block — see data/options.py:group_of. It is an inference from
    how the ids are laid out rather than a join, so the set is offered for you to
    confirm against your screen, never applied on its own.
    """
    # Coerced, not merely passed on: dataset.get() ints it for the lookup, but
    # the sibling filter below compares ids directly, so a string id from a query
    # string would leave the picked option in its own sibling list.
    option_id = _int(body.get("option_id"))
    entry = dataset.get("option", option_id)
    if entry is None:
        raise ApiError(404, f"unknown option {option_id}")
    group = entry.get("group")
    # Excluded by *text*, not by id. Dropping only `option_id` left the other
    # four "Gather more of the deep data" stages in the list, so picking one
    # stage of a gamble offered you the same line again as though it were
    # something else to add.
    same_line = (entry.get("name"), entry.get("desc"))
    siblings = [o for o in dataset.load()["options"]
                if o.get("group") == group and o["id"] != option_id
                and (o.get("name"), o.get("desc")) != same_line]
    # Folded here too, or the sibling list is the same noise the search was:
    # this group's "6 more options" are really two distinct lines repeated.
    return {
        "group": group,
        "picked": entry,
        "options": _fold_repeats(sorted(siblings, key=lambda o: o["id"])),
    }


def inventory_reconcile(body: dict) -> dict:
    return inventory.reconcile(_run(body), body.get("scanned") or {},
                               complete=body.get("complete", False))


def weighted(body: dict) -> dict:
    """The whole Weighted Curio pool — 17, so it is browsed rather than searched.

    The equip screen shows icons with no names on them, so searching by name
    means clicking every tile in game first. At this size the honest interface is
    the full list with a tick against each.
    """
    return {"weighted": dataset.load()["weighted_curios"]}


def weighted_rank(body: dict) -> dict:
    """Which of the ones you hold belong in the sockets."""
    return owned.weighted_plan(_run(body, key=None))


def domains(body: dict) -> dict:
    ds = dataset.load()
    return {"domains": ds["domains"], "beacons": ds["beacons"], "deck": ds.get("deck", {})}


def changelog(body: dict) -> dict:
    """What changed on the site, parsed out of CHANGELOG.md.

    Parsed rather than transcribed into a JSON file at build time, for the same
    reason `engine/explain.py` reads live constants: a second copy is a copy
    that can silently fall behind the thing it claims to describe.

    The file ships inside python.zip, so this is one implementation for both
    transports — `Path(__file__).parent.parent` is the repo root locally and the
    zip root in the browser, which is why the archive mirrors the repo layout.

    Each `##` release holds `###` categories, the way a game's patch notes are
    laid out: what is new, what changed, what was broken. A category line that
    the parser did not understand would be *silently dropped* from the tab while
    still sitting in the file, so the two would disagree about what shipped with
    nothing to indicate it. That is why this understands the heading rather than
    the file working around the parser.

    Bullets written before any category still parse, into a group with no title,
    so an older section and a hand-written note both survive.
    """
    src = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    if not src.exists():
        return {"entries": [], "note": "This build does not include CHANGELOG.md."}

    entries: list[dict] = []
    current: dict | None = None
    group: dict | None = None

    def _add_group(title: str) -> dict:
        g = {"title": title, "items": []}
        current["groups"].append(g)
        return g

    for line in src.read_text(encoding="utf-8").splitlines():
        # `### x` does not start with `## ` (the third character is a hash, not
        # a space), so the release check cannot swallow a category.
        if line.startswith("## "):
            current = {"title": line[3:].strip(), "groups": [], "items": []}
            entries.append(current)
            group = None
        elif current is not None and line.startswith("### "):
            group = _add_group(line[4:].strip())
        elif current is not None and line.strip().startswith("- "):
            if group is None:
                group = _add_group("")
            text = line.strip()[2:].strip()
            group["items"].append(text)
            current["items"].append(text)
        elif (current is not None and group is not None and group["items"]
                and line.startswith(("  ", "\t"))):
            # A wrapped bullet. Joined rather than dropped, since the entries
            # are prose and a truncated sentence reads as a different claim.
            group["items"][-1] += " " + line.strip()
            current["items"][-1] += " " + line.strip()

    # A section with nothing under it is dropped rather than rendered as a bare
    # heading. `du publish` stamps the release *after* the deploy, so between a
    # publish and the next thing written down `## Unreleased` sits empty — and
    # the tab labels the top section "This build", which would have shown
    # visitors a heading for the build they are running with no content beneath
    # it. The build id is already stated above the list, so there is nothing
    # lost by saying nothing. A category with nothing under it goes the same way,
    # for the same reason one level down.
    for e in entries:
        e["groups"] = [g for g in e["groups"] if g["items"]]
    return {"entries": [e for e in entries if e["items"]]}


def explain_scoring(body: dict) -> dict:
    """Everything the engine values, plus what it evaluates to for this run.

    Read live from the modules that use the numbers, never transcribed — see
    engine/explain.py. The run is optional in practice: with a default RunState
    the constants are still correct and only the "right now" column is generic.
    """
    return explain.payload(_run(body, key=None))


# ----------------------------------------------------------------------- OCR
# Kept in the router so route parity holds, but every one of these degrades
# cleanly when the engine is absent — which is the hosted build's normal state,
# not an error (WEB-PLAN.md Phase 3).

def _ocr_module():
    """The OCR engine, or a clean 503.

    On the hosted build `resolve/` is not shipped at all, so this is an
    ImportError rather than a missing model file. Either way the answer the UI
    needs is "unavailable", not a traceback — see WEB-PLAN.md Phase 3.
    """
    try:
        from resolve import ocr
    except ImportError:
        raise ApiError(503, "This build cannot read screenshots.")
    return ocr


def ocr_status(body: dict) -> dict:
    try:
        return {"available": _ocr_module().available()}
    except Exception:
        return {"available": False}


def _ocr_bytes(body: dict) -> bytes:
    ocr = _ocr_module()
    if not ocr.available():
        raise ApiError(503, "The OCR engine is not installed.")
    data = body.get("data") or b""
    if not data:
        raise ApiError(400, "empty upload")
    return data


def ocr_inventory(body: dict) -> dict:
    """Scan an inventory screenshot. Returns findings only — nothing is applied."""
    data = _ocr_bytes(body)
    try:
        return _ocr_module().read_inventory(data)
    except ApiError:
        raise
    except Exception as e:
        raise ApiError(422, f"could not read image: {e}")


def ocr_options(body: dict) -> dict:
    """Read an Occurrence or shop screen, including the live costs shown on it."""
    data = _ocr_bytes(body)
    try:
        return {"lines": _ocr_module().read_options(data)}
    except ApiError:
        raise
    except Exception as e:
        raise ApiError(422, f"could not read image: {e}")


def ocr_offer(body: dict) -> dict:
    """Read an offer screenshot into candidates awaiting confirmation.

    Results are always returned as candidate lists, never applied directly — a
    wrong auto-resolve corrupts the run state silently, which is the failure
    this tool exists to avoid.
    """
    data = _ocr_bytes(body)
    try:
        return {"cards": _ocr_module().read_offer(data, kind=body.get("kind", "blessing"))}
    except ApiError:
        raise
    except Exception as e:                   # a bad crop should not 500 the app
        raise ApiError(422, f"could not read image: {e}")


# ------------------------------------------------------------------ dispatch

ROUTES: dict[str, Callable[[dict], Any]] = {
    "/api/meta": meta,
    "/api/search": search,
    "/api/resolve": resolve_one,
    "/api/resolve_many": resolve_batch,
    "/api/rank": rank,
    "/api/equations": equations_status,
    "/api/run/save": run_save,
    "/api/run/load": run_load,
    "/api/run/history": run_history,
    "/api/run/restore": run_restore,
    "/api/masks/rank": masks_rank,
    "/api/miracles/rank": miracles_rank,
    "/api/deck/targets": deck_targets,
    "/api/miracles/pool": miracles_pool,
    "/api/owned": owned_summary,
    "/api/waypoint": waypoint_doors,
    "/api/workbench": workbench,
    "/api/offer": offer,
    "/api/store": store_shelf,
    "/api/options/set": option_set,
    "/api/inventory/reconcile": inventory_reconcile,
    "/api/weighted": weighted,
    "/api/weighted/rank": weighted_rank,
    "/api/domains": domains,
    "/api/explain": explain_scoring,
    "/api/changelog": changelog,
    "/api/ocr/status": ocr_status,
    "/api/ocr/inventory": ocr_inventory,
    "/api/ocr/options": ocr_options,
    "/api/ocr": ocr_offer,
}


def dispatch(path: str, body: dict | None = None) -> Any:
    """Run one API route. The only entry point either shim should use.

    A query string on `path` is parsed into the body. FastAPI does that for its
    own routes before ever reaching here, so this is a no-op for that shim — but
    the worker hands the URL over verbatim, and `app.js` builds two of them with
    a query string (`/api/search`, `/api/options/set`). Without this they matched
    no key in ROUTES and 404'd on the hosted build only: search returned nothing
    however you typed, and the Spend tab's sibling options failed silently.

    Values arrive as text, since a query string has no types. Handlers that want
    a number coerce it themselves rather than dispatch guessing — a search for
    "617" is a string and must stay one.
    """
    path, _, query = path.partition("?")
    handler = ROUTES.get(path)
    if handler is None:
        raise ApiError(404, f"unknown route {path}")
    if query:
        # An explicit body wins, so a caller can always override the URL.
        body = {**dict(parse_qsl(query)), **(body or {})}
    return handler(body or {})
