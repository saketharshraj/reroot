"""ReRoot API. Contract: contracts/openapi.yaml. Runs on Cloud Run or Render.

    uv run uvicorn reroot.api.main:app --reload --port 8080
"""

import logging
import os
import threading
import time
from contextlib import asynccontextmanager

import json

import ee
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from reroot.api import agent as agent_mod
from reroot.api import alerts as alerts_mod
from reroot.api import assess as assess_mod
from reroot.api import monitor as monitor_mod
from reroot.api import plan as plan_mod
from reroot.api import sites as sites_mod
from reroot.api import verify as verify_mod
from reroot.core import config, layers as L
from reroot.core.clients import gemini, init_ee

REGION_NAMES = {"aravalli_ggn_fbd": "Aravalli belt · Gurugram–Faridabad"}
TILE_TTL_S = 3 * 3600  # EE map IDs expire; refresh well before they do


def _warm_up() -> None:
    """Preload slow, deterministic results so the first demo run after a cold start is fast."""
    try:
        from reroot.api import basemap
        basemap._basemap(config.DEFAULT_REGION)
        demo = agent_mod.DEMO_PROJECT
        a = assess_mod.assess(demo)
        sites_mod.search({"project_geometry": demo, "area_ha_needed": float(a["offset_required_ha"]), "radius_km": 15.0,
                          "exclude": ["farmland", "built", "water", "grassland", "steep"],
                          "weights": dict(agent_mod.BASE_WEIGHTS), "region": config.DEFAULT_REGION})
        from reroot.api import policy
        policy.warm_snapshots()  # live fetch where possible, else the stored copies
        logging.getLogger(__name__).info("warm-up done")
    except Exception:  # noqa: BLE001 - warm-up is best effort
        logging.getLogger(__name__).exception("warm-up failed")


_startup_error: str | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _startup_error
    try:
        init_ee(high_volume=True)
        if os.getenv("WARM_UP", "1") == "1":
            threading.Thread(target=_warm_up, daemon=True).start()
    except Exception as e:  # noqa: BLE001 - keep serving so /health can explain what's wrong
        _startup_error = f"Google credentials/Earth Engine init failed: {e}"
        logging.getLogger(__name__).exception("startup failed")
    yield


app = FastAPI(title="ReRoot API", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")  # not /healthz: Cloud Run reserves paths ending in "z"
def health(deep: bool = False):
    """Cheap by default (hosts poll it); ?deep=true also round-trips Earth Engine and Gemini."""
    out = {"ok": _startup_error is None, "project": config.PROJECT}
    if _startup_error:
        from reroot.core import auth
        path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        diag = {"adc_error": auth.LAST_ADC_ERROR, "credentials_env_json_set": bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON"))}
        if path:  # existence and size only, never contents
            diag |= {"credentials_file": path, "exists": os.path.exists(path),
                     "size_bytes": os.path.getsize(path) if os.path.exists(path) else None,
                     "secrets_files": sorted(os.path.relpath(os.path.join(d, f), "/etc/secrets")
                                             for d, _, fs in os.walk("/etc/secrets", followlinks=True) for f in fs)
                     if os.path.isdir("/etc/secrets") else None}
        return {**out, "error": _startup_error, "diagnostics": diag}
    if deep:
        out["ee"] = ee.Number(1).getInfo() == 1
        r = gemini().models.generate_content(model=config.GEMINI_MODEL_FAST, contents="Reply with exactly: pong")
        out["gemini"] = r.text.strip()
    return out


@app.get("/v1/regions")
def regions():
    return [{"id": k, "name": REGION_NAMES.get(k, k), "bbox": v["bbox"]} for k, v in config.REGIONS.items()]


def _layer_image(layer: str, aoi: ee.Geometry) -> tuple[ee.Image, dict, list[str]]:
    s2 = lambda: L.s2_composite(aoi, "2025-11-01", "2026-03-01")  # noqa: E731
    if layer == "true_colour":
        return s2(), L.RGB_VIS, [L.S2, L.CLOUD_SCORE_PLUS]
    if layer == "ndvi":
        return L.ndvi(s2()), L.NDVI_VIS, [L.S2, L.CLOUD_SCORE_PLUS]
    if layer == "land_cover":
        return L.dynamic_world_mode(aoi, "2025-01-01", "2026-01-01"), L.DW_VIS, [L.DYNAMIC_WORLD]
    if layer == "rainfall":
        return L.annual_rainfall_mm(aoi, 2025), {"min": 300, "max": 1000, "palette": ["fff7bc", "41b6c4", "253494"]}, [L.CHIRPS]
    if layer == "slope":
        return L.slope_deg(aoi), {"min": 0, "max": 25, "palette": ["ffffff", "fdae61", "d7191c"]}, [L.DEM]
    if layer == "similarity":
        sim = L.similarity_to_point(L.embeddings(aoi, config.CURRENT_YEAR), ee.Geometry.Point(config.DEMO_REFERENCE_POINT))
        return sim, {"min": 0.6, "max": 1.0, "palette": ["000004", "8c2981", "fe9f6d", "fcfdbf"]}, [L.EMBEDDINGS]
    raise HTTPException(404, f"unknown layer {layer}")


_tiles: dict[tuple[str, str], tuple[float, dict]] = {}


@app.get("/v1/layers/{layer}")
def layer_tiles(layer: str, region: str = config.DEFAULT_REGION):
    if region not in config.REGIONS:
        raise HTTPException(404, f"unknown region {region}")
    hit = _tiles.get((layer, region))
    if hit and time.time() - hit[0] < TILE_TTL_S:
        return hit[1]
    img, vis, sources = _layer_image(layer, L.bbox(config.REGIONS[region]["bbox"]))
    out = {"tile_url": img.getMapId(vis)["tile_fetcher"].url_format, "legend": vis, "sources": sources}
    _tiles[(layer, region)] = (time.time(), out)
    return out


class AssessRequest(BaseModel):
    geometry: dict


@app.post("/v1/assess")
def assess(req: AssessRequest):
    if req.geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise HTTPException(422, "geometry must be a GeoJSON Polygon or MultiPolygon")
    return assess_mod.assess(req.geometry)


class SiteSearchRequest(BaseModel):
    project_geometry: dict
    area_ha_needed: float = Field(gt=0, le=5000)
    radius_km: float = Field(default=15, gt=0, le=40)
    exclude: list[str] = ["farmland", "built", "water", "grassland", "steep"]
    weights: dict[str, float] = {}
    region: str = config.DEFAULT_REGION


@app.post("/v1/sites/search")
def sites_search(req: SiteSearchRequest):
    if req.region not in config.REGIONS:
        raise HTTPException(404, f"unknown region {req.region}")
    return sites_mod.search(req.model_dump())


# --- Studio: agent, plan, monitoring, alerts, photo proof -------------------------------------


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    workspace: dict = {}


@app.post("/v1/agent/chat")
def agent_chat(req: ChatRequest):
    def sse():
        for event in agent_mod.run(req.message, req.history, req.workspace):
            yield f"data: {json.dumps(event, default=str)}\n\n"
    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class PlanRequest(BaseModel):
    project_geometry: dict
    assess: dict
    sites: list[dict]
    profile: dict = {}


@app.post("/v1/plan")
def make_plan(req: PlanRequest):
    if not req.sites:
        raise HTTPException(422, "no sites selected")
    return plan_mod.build(req.project_geometry, req.assess, req.sites, req.profile)


@app.get("/v1/plans/{plan_id}/map.jpg")
def plan_map(plan_id: str):
    data = alerts_mod._bucket().blob(f"plans/{plan_id}.jpg").download_as_bytes()
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/v1/media/{name}")
def media(name: str):
    if not name.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(400, "bad name")
    data = alerts_mod._bucket().blob(f"media/{name}").download_as_bytes()
    return Response(data, media_type="image/gif" if name.endswith(".gif") else "application/octet-stream",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/v1/plantations")
def plantations():
    return monitor_mod.plantations()


@app.get("/v1/plantations/{plantation_id}/timeseries")
def plantation_timeseries(plantation_id: str):
    return monitor_mod.timeseries(plantation_id)


@app.get("/v1/plantations/{plantation_id}/check")
def plantation_check(plantation_id: str):
    c = monitor_mod.check(plantation_id)
    if not c:
        raise HTTPException(404, f"unknown plantation {plantation_id}")
    return c[0]


class MonitorRunRequest(BaseModel):
    plantation_id: str | None = None


@app.post("/v1/monitor/run")
def monitor_run(req: MonitorRunRequest):
    return alerts_mod.evaluate_and_publish(req.plantation_id)


@app.get("/v1/alerts")
def list_alerts(plantation_id: str | None = None, limit: int = 20):
    return alerts_mod.list_alerts(plantation_id, min(limit, 100))


@app.get("/v1/alerts/{alert_id}/audio")
def alert_audio(alert_id: str):
    return Response(alerts_mod.audio(alert_id), media_type="audio/mpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.post("/pubsub/alerts")
async def pubsub_alerts(request: Request, authorization: str | None = Header(default=None)):
    try:
        alerts_mod.verify_push(authorization)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    row = alerts_mod.handle_push(await request.json())
    return {"ok": True, "alert_id": row["alert_id"]}


@app.post("/v1/verify-photo")
async def verify_photo(plantation_id: str = Form(...), photo: UploadFile = File(...),
                       lon: float | None = Form(default=None), lat: float | None = Form(default=None)):
    data = await photo.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "photo too large (15 MB max)")
    return verify_mod.verify(data, photo.content_type or "image/jpeg", plantation_id, lon, lat)
