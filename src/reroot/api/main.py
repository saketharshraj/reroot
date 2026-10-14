"""ReRoot API. Contract: contracts/openapi.yaml. Runs on Cloud Run or Render.

    uv run uvicorn reroot.api.main:app --reload --port 8080
"""

import os
import time
from contextlib import asynccontextmanager

import ee
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from reroot.api import assess as assess_mod
from reroot.api import sites as sites_mod
from reroot.core import config, layers as L
from reroot.core.clients import gemini, init_ee

REGION_NAMES = {"aravalli_ggn_fbd": "Aravalli belt · Gurugram–Faridabad"}
TILE_TTL_S = 3 * 3600  # EE map IDs expire; refresh well before they do


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_ee(high_volume=True)
    yield


app = FastAPI(title="ReRoot API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")  # not /healthz: Cloud Run reserves paths ending in "z"
def health(deep: bool = False):
    """Cheap by default (hosts poll it); ?deep=true also round-trips Earth Engine and Gemini."""
    out = {"ok": True, "project": config.PROJECT}
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
