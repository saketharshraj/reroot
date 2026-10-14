"""ReRoot API (Cloud Run service `api`). Contract: contracts/openapi.yaml.

    uv run uvicorn reroot.api.main:app --reload --port 8080
"""

from contextlib import asynccontextmanager
from functools import lru_cache

import ee
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from reroot.core import config, layers as L
from reroot.core.clients import gemini, init_ee


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_ee(high_volume=True)
    yield


app = FastAPI(title="ReRoot API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

REGIONS = {"aravalli_ggn_fbd": config.DEMO_AOI_BBOX}


@app.get("/health")  # not /healthz: Cloud Run reserves paths ending in "z"
def health(deep: bool = False):
    out = {"ok": True, "project": config.PROJECT, "ee": ee.Number(1).getInfo() == 1}
    if deep:
        r = gemini().models.generate_content(model=config.GEMINI_MODEL_FAST, contents="Reply with exactly: pong")
        out["gemini"] = r.text.strip()
    return out


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
        sim = L.similarity_to_point(L.embeddings(aoi, 2024), ee.Geometry.Point(config.DEMO_REFERENCE_POINT))
        return sim, {"min": 0.6, "max": 1.0, "palette": ["000004", "8c2981", "fe9f6d", "fcfdbf"]}, [L.EMBEDDINGS]
    raise HTTPException(404, f"unknown layer {layer}")


@lru_cache(maxsize=64)
def _tile_url(layer: str, region: str) -> tuple[str, dict, tuple[str, ...]]:
    img, vis, sources = _layer_image(layer, L.bbox(REGIONS[region]))
    return img.getMapId(vis)["tile_fetcher"].url_format, vis, tuple(sources)


@app.get("/v1/layers/{layer}")
def layer_tiles(layer: str, region: str = "aravalli_ggn_fbd"):
    if region not in REGIONS:
        raise HTTPException(404, f"unknown region {region}")
    url, vis, sources = _tile_url(layer, region)
    return {"tile_url": url, "legend": vis, "sources": list(sources)}
