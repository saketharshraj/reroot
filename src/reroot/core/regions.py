"""Region registry: the built-in regions in config.REGIONS plus regions users load on demand.

On-demand regions live in BigQuery `reroot.regions` with a build status the Studio polls:
queued -> exporting -> building -> scoring -> rendering -> ready (or failed).
"""

import math
import re
import time

from google.cloud import bigquery

from reroot.core import config
from reroot.core.clients import bq

TABLE = f"{config.PROJECT}.{config.BQ_DATASET}.regions"
BUILT_IN_NAMES = {"aravalli_ggn_fbd": "Aravalli belt · Gurugram–Faridabad"}
_cache: tuple[float, dict] | None = None
CACHE_S = 10

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS `{TABLE}` (
  region_id STRING NOT NULL, name STRING, bbox ARRAY<FLOAT64>, utm STRING,
  center_lon FLOAT64, center_lat FLOAT64, status STRING, progress FLOAT64, message STRING,
  execution STRING, created_at TIMESTAMP, updated_at TIMESTAMP
)"""


def utm_for(lon: float, lat: float) -> str:
    zone = int((lon + 180) // 6) + 1
    return f"EPSG:{32600 if lat >= 0 else 32700}{zone:02d}"


def _built_in() -> dict[str, dict]:
    return {k: {"region_id": k, "name": BUILT_IN_NAMES.get(k, k), "bbox": v["bbox"], "utm": v["utm"],
                "status": "ready", "progress": 1.0, "message": None, "built_in": True}
            for k, v in config.REGIONS.items()}


def all_regions(fresh: bool = False) -> dict[str, dict]:
    global _cache
    if not fresh and _cache and time.time() - _cache[0] < CACHE_S:
        return _cache[1]
    out = _built_in()
    try:
        for r in bq().query(f"SELECT * FROM `{TABLE}` ORDER BY created_at").result():
            out[r.region_id] = {"region_id": r.region_id, "name": r.name, "bbox": list(r.bbox), "utm": r.utm,
                                "status": r.status, "progress": r.progress, "message": r.message,
                                "center": [r.center_lon, r.center_lat], "built_in": False,
                                "updated_at": r.updated_at.isoformat() if r.updated_at else None}
    except Exception:  # noqa: BLE001 - table may not exist yet
        pass
    _cache = (time.time(), out)
    return out


def spec(region_id: str) -> dict:
    regions = all_regions()
    if region_id not in regions:
        regions = all_regions(fresh=True)
    return regions[region_id]


def containing(lon: float, lat: float, ready_only: bool = True) -> str | None:
    for rid, r in all_regions().items():
        w, s, e, n = r["bbox"]
        if w <= lon <= e and s <= lat <= n and (r["status"] == "ready" or not ready_only):
            return rid
    return None


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "region"


def create_bbox(lon: float, lat: float, size_km: float = 12) -> list[float]:
    size_km = max(5.0, min(float(size_km), 25.0))  # keep builds to ~5-15 minutes
    dlat = size_km / 2 / 111.32
    dlon = size_km / 2 / (111.32 * math.cos(math.radians(lat)))
    return [round(lon - dlon, 5), round(lat - dlat, 5), round(lon + dlon, 5), round(lat + dlat, 5)]


def create(name: str, lon: float, lat: float, size_km: float = 12) -> dict:
    rid = f"{slug(name)}_{int(time.time()) % 100000}"
    bbox = create_bbox(lon, lat, size_km)
    bq().query(SCHEMA_SQL).result()
    bq().query(f"""INSERT INTO `{TABLE}` (region_id, name, bbox, utm, center_lon, center_lat, status, progress,
                   message, created_at, updated_at)
                   VALUES (@id, @name, @bbox, @utm, @lon, @lat, 'queued', 0, 'Queued', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())""",
               job_config=bigquery.QueryJobConfig(query_parameters=[
                   bigquery.ScalarQueryParameter("id", "STRING", rid),
                   bigquery.ScalarQueryParameter("name", "STRING", name),
                   bigquery.ArrayQueryParameter("bbox", "FLOAT64", bbox),
                   bigquery.ScalarQueryParameter("utm", "STRING", utm_for(lon, lat)),
                   bigquery.ScalarQueryParameter("lon", "FLOAT64", lon),
                   bigquery.ScalarQueryParameter("lat", "FLOAT64", lat)])).result()
    return spec(rid)


def set_status(region_id: str, status: str, progress: float, message: str, execution: str | None = None) -> None:
    if region_id in config.REGIONS:
        return  # built-in regions have no status row
    bq().query(f"""UPDATE `{TABLE}` SET status = @s, progress = @p, message = @m,
                   execution = COALESCE(@x, execution), updated_at = CURRENT_TIMESTAMP() WHERE region_id = @id""",
               job_config=bigquery.QueryJobConfig(query_parameters=[
                   bigquery.ScalarQueryParameter("s", "STRING", status),
                   bigquery.ScalarQueryParameter("p", "FLOAT64", round(progress, 3)),
                   bigquery.ScalarQueryParameter("m", "STRING", message[:300]),
                   bigquery.ScalarQueryParameter("x", "STRING", execution),
                   bigquery.ScalarQueryParameter("id", "STRING", region_id)])).result()


JOB = f"projects/{config.PROJECT}/locations/{config.REGION}/jobs/region-builder"


def start_build(region_id: str) -> str:
    """Run the Cloud Run Job that exports, builds, scores and renders the region."""
    from google.cloud import run_v2

    from reroot.core.auth import get_credentials
    client = run_v2.JobsClient(credentials=get_credentials())
    op = client.run_job(request=run_v2.RunJobRequest(
        name=JOB,
        overrides=run_v2.RunJobRequest.Overrides(container_overrides=[
            run_v2.RunJobRequest.Overrides.ContainerOverride(
                args=["-m", "reroot.pipelines.build_grid", "build", "--region", region_id])])))
    execution = op.metadata.name if getattr(op, "metadata", None) else ""
    set_status(region_id, "queued", 0.02, "Build job started", execution=execution)
    return execution


def eta_minutes(bbox: list[float]) -> int:
    w, s, e, n = bbox
    km = max((e - w) * 111.32 * math.cos(math.radians((s + n) / 2)), (n - s) * 111.32)
    return int(4 + km * 0.6)


def public(r: dict) -> dict:
    return {k: r.get(k) for k in ("region_id", "name", "bbox", "status", "progress", "message", "built_in")} | {
        "eta_minutes": eta_minutes(r["bbox"]) if r["status"] not in ("ready", "failed") else None}
