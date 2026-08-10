"""Local web app: run tracker and decision panel.

    python -m web.app          then open http://127.0.0.1:8765

Everything runs locally against the built dataset. Nothing is sent anywhere.

This module is a *shim*: it validates with pydantic and hands plain dicts to
`web/router.py`, which owns every route body. No scoring, no dataset joins, no
decisions live here — see the header of that file for why.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine.state import RunState
from web.router import ApiError, dispatch

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="DU Companion")


def call(path: str, body: dict | None = None):
    """Run a route, translating the router's transport-agnostic error."""
    try:
        return dispatch(path, body or {})
    except ApiError as e:
        raise HTTPException(e.status, e.detail)


async def upload(file: UploadFile) -> bytes:
    if file is None:
        raise HTTPException(400, "empty upload")
    return await file.read()


# ------------------------------------------------------------------ schemas

class TeamMember(BaseModel):
    name: str
    path: str = ""
    element: str = ""


class RunPayload(BaseModel):
    mask_id: int | None = None
    wishpower_level: int = 0
    plane: int = 1
    difficulty: int = 1
    domain_index: int = 1
    domain_total: int = 13
    team: list[TeamMember] = []
    owned_blessings: list[int] = []
    enhanced_blessings: list[int] = []
    owned_curios: list[int] = []
    owned_weighted: list[int] = []
    equipped_weighted: list[int] = []
    weighted_slots: int = 2
    owned_equations: list[int] = []
    owned_miracles: list[int] = []
    miracle_resets: int = 0
    door_redraws: int = 0
    fragments: int = 0
    heat: int = 0
    heat_max: int = 0
    heat_costs: dict[str, int] = {"Common": 1, "Rare": 2, "Legendary": 3}
    heat_per_enhance: int = 1
    store_prices: dict[str, int] = {"Common": 100, "Rare": 180, "Legendary": 300}
    blessing_prices: dict[str, int] = {"Common": 80, "Rare": 120, "Legendary": 180}
    notes: str = ""

    # Transport-level, not part of the run. Declared because pydantic drops
    # unknown keys, so without it the flag would vanish on the HTTP path while
    # still working in the worker -- the two transports disagreeing, which is
    # exactly what web/router.py exists to prevent.
    force_snapshot: bool = False

    def to_state(self) -> RunState:
        # Deliberately the same path the router takes, so the two transports
        # cannot default a field differently. test_router.py pins this.
        return RunState.from_dict(self.model_dump())


class DoorSpec(BaseModel):
    name: str
    beacons: list[int] = []
    level: int | None = None
    # For a whole draw pile, naming every beacon is more typing than the decision
    # is worth; a count is enough to rank cards against each other.
    beacon_count: int = 0


class WaypointRequest(BaseModel):
    run: RunPayload
    doors: list[DoorSpec] = []
    redraws_remaining: int | None = None


class WorkbenchRequest(BaseModel):
    run: RunPayload
    candidate_ids: list[int] | None = None


class OfferRequest(BaseModel):
    run: RunPayload
    option_ids: list[int] = []
    costs: dict[str, int] = {}
    refresh_cost: int = 0
    offered_entry_ids: list[int] = []
    offered_kind: str = "blessing"


class ShelfItem(BaseModel):
    id: int
    # Whatever the card actually charges. Prefilled from `store_prices` by
    # rarity, but the printed number wins — no price table is datamined.
    cost: int = 0


class StoreRequest(BaseModel):
    run: RunPayload
    items: list[ShelfItem] = []
    refresh_cost: int = 0
    refreshes_left: int | None = None
    # Herta's shelf sells Curios; the Blessing Store sells Blessings and prices
    # them differently. Same decision, different pool and different scorer.
    kind: str = "curio"


class ReconcileRequest(BaseModel):
    run: RunPayload
    scanned: dict[str, list[int]] = {}
    complete: bool = False


class RankRequest(BaseModel):
    run: RunPayload
    kind: str = "blessing"
    ids: list[int] = []


class ResolveRequest(BaseModel):
    text: str = ""
    desc_text: str = ""
    kind: str | None = None
    path: str | None = None
    rarity: str | None = None


class ResolveManyRequest(BaseModel):
    observations: list[ResolveRequest] = []
    kind: str | None = None


class RestoreRequest(BaseModel):
    file: str


class MaskRankRequest(BaseModel):
    run: RunPayload
    mask_ids: list[int] = []


class MiracleRankRequest(BaseModel):
    run: RunPayload
    miracle_ids: list[int] = []
    resets_remaining: int | None = None


class TargetRequest(BaseModel):
    run: RunPayload
    cards: list[DoorSpec] = []
    intent: str = "sacrifice"
    restricted_to: list[str] = []
    miracle_id: int | None = None


# ------------------------------------------------------------------- routes
# Each of these is a transport adapter and nothing more: validate, hand a plain
# dict to the router, return what comes back.

@app.get("/api/meta")
def api_meta():
    return call("/api/meta")


@app.get("/api/search")
def api_search(q: str, kind: str | None = None, limit: int = 15):
    return call("/api/search", {"q": q, "kind": kind, "limit": limit})


@app.post("/api/resolve")
def api_resolve(req: ResolveRequest):
    return call("/api/resolve", req.model_dump())


@app.post("/api/resolve_many")
def api_resolve_many(req: ResolveManyRequest):
    return call("/api/resolve_many", req.model_dump())


@app.post("/api/rank")
def api_rank(req: RankRequest):
    return call("/api/rank", req.model_dump())


@app.post("/api/equations")
def api_equations(req: RunPayload):
    return call("/api/equations", req.model_dump())


@app.post("/api/run/save")
def api_save(req: RunPayload):
    return call("/api/run/save", req.model_dump())


@app.get("/api/run/load")
def api_load():
    return call("/api/run/load")


@app.get("/api/run/history")
def api_history():
    return call("/api/run/history")


@app.post("/api/run/restore")
def api_restore(req: RestoreRequest):
    return call("/api/run/restore", req.model_dump())


@app.post("/api/masks/rank")
def api_rank_masks(req: MaskRankRequest):
    return call("/api/masks/rank", req.model_dump())


@app.post("/api/miracles/rank")
def api_rank_miracles(req: MiracleRankRequest):
    return call("/api/miracles/rank", req.model_dump())


@app.post("/api/deck/targets")
def api_targets(req: TargetRequest):
    return call("/api/deck/targets", req.model_dump())


@app.post("/api/miracles/pool")
def api_miracle_pool(req: RunPayload):
    return call("/api/miracles/pool", req.model_dump())


@app.post("/api/owned")
def api_owned(req: RunPayload):
    return call("/api/owned", req.model_dump())


@app.post("/api/waypoint")
def api_waypoint(req: WaypointRequest):
    return call("/api/waypoint", req.model_dump())


@app.post("/api/workbench")
def api_workbench(req: WorkbenchRequest):
    return call("/api/workbench", req.model_dump())


@app.post("/api/offer")
def api_offer(req: OfferRequest):
    return call("/api/offer", req.model_dump())


@app.post("/api/store")
def api_store(req: StoreRequest):
    return call("/api/store", req.model_dump())


@app.get("/api/options/set")
def api_option_set(option_id: int):
    return call("/api/options/set", {"option_id": option_id})


@app.post("/api/inventory/reconcile")
def api_reconcile(req: ReconcileRequest):
    return call("/api/inventory/reconcile", req.model_dump())


@app.get("/api/weighted")
def api_weighted(req_run: str | None = None):
    return call("/api/weighted")


@app.post("/api/weighted/rank")
def api_weighted_rank(req: RunPayload):
    return call("/api/weighted/rank", req.model_dump())


@app.get("/api/domains")
def api_domains():
    return call("/api/domains")


@app.post("/api/explain")
def api_explain(req: RunPayload):
    return call("/api/explain", req.model_dump())


@app.get("/api/changelog")
def api_changelog():
    return call("/api/changelog")


@app.get("/api/ocr/status")
def api_ocr_status():
    return call("/api/ocr/status")


@app.post("/api/ocr/inventory")
async def api_ocr_inventory(file: UploadFile = File(...)):
    return call("/api/ocr/inventory", {"data": await upload(file)})


@app.post("/api/ocr/options")
async def api_ocr_options(file: UploadFile = File(...)):
    return call("/api/ocr/options", {"data": await upload(file)})


@app.post("/api/ocr")
async def api_ocr(kind: str = "blessing", file: UploadFile = File(...)):
    return call("/api/ocr", {"data": await upload(file), "kind": kind})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main() -> None:
    import uvicorn
    print("DU Companion -> http://127.0.0.1:8765")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    main()
