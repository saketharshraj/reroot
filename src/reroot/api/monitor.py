"""Plantation health (satellite NDVI vs expected trajectory) and live weather risk."""

import json
import time
from datetime import date, datetime, timedelta, timezone

import ee
from google.cloud import bigquery

from reroot.core import config
from reroot.core.clients import bq

DS = f"{config.PROJECT}.{config.BQ_DATASET}"
GFS = "NOAA/GFS0P25"
GSMAP = "JAXA/GPM_L3/GSMaP/v8/operational"
SMAP = "NASA/SMAP/SPL4SMGP/008"
WEATHER_SOURCES = [GFS, GSMAP, SMAP]

# Establishment index = recent NDVI / expected NDVI (mature-forest reference, ramped by age).
STATUS_BANDS = [(0.9, "on_track"), (0.75, "watch"), (0.0, "at_risk")]
DRY_FORECAST_MM = 5.0   # less than this in the next 10 days
DRY_PAST_MM = 10.0      # and less than this in the last 14 days -> dry spell

PLANTATIONS_SQL = f"""
WITH recent AS (
  SELECT plantation_id, AVG(ndvi_mean) AS ndvi, AVG(ndvi_expected) AS expected, MAX(date) AS last_obs
  FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY plantation_id ORDER BY date DESC) AS rn
    FROM `{DS}.plantation_timeseries`
    WHERE ndvi_mean IS NOT NULL AND ndvi_expected IS NOT NULL
  ) WHERE rn <= 6 GROUP BY 1
)
SELECT p.plantation_id, p.name, p.planted_date, p.species, p.saplings_planted, p.caretaker_name,
  p.caretaker_chat_id, p.language, p.source_project, IFNULL(p.is_demo, FALSE) AS is_demo,
  ST_ASGEOJSON(p.geom) AS geojson, ST_AREA(p.geom) / 1e4 AS area_ha,
  ST_X(ST_CENTROID(p.geom)) AS lon, ST_Y(ST_CENTROID(p.geom)) AS lat,
  r.ndvi, r.expected, SAFE_DIVIDE(r.ndvi, r.expected) AS index, r.last_obs
FROM `{DS}.plantations` p LEFT JOIN recent r USING (plantation_id)
ORDER BY p.plantation_id
"""


def _status(index: float | None) -> str:
    if index is None:
        return "unknown"
    return next(name for floor, name in STATUS_BANDS if index >= floor)


def plantations() -> list[dict]:
    out = []
    for r in bq().query(PLANTATIONS_SQL).result():
        age = (date.today() - r.planted_date).days / 365.25 if r.planted_date else None
        out.append({
            "plantation_id": r.plantation_id, "name": r.name, "planted_date": r.planted_date.isoformat(),
            "age_years": round(age, 1) if age is not None else None,
            "species": list(r.species or []), "saplings_planted": r.saplings_planted,
            "caretaker_name": r.caretaker_name, "language": r.language, "source_project": r.source_project,
            "is_demo": r.is_demo, "geometry": json.loads(r.geojson), "area_ha": round(r.area_ha, 1),
            "centroid": [r.lon, r.lat],
            "ndvi_recent": round(r.ndvi, 3) if r.ndvi is not None else None,
            "ndvi_expected": round(r.expected, 3) if r.expected is not None else None,
            "establishment_index": round(r.index, 2) if r.index is not None else None,
            "status": _status(r.index),
            "last_observation": r.last_obs.isoformat() if r.last_obs else None,
            "sources": ["COPERNICUS/S2_SR_HARMONIZED", f"{DS}.plantation_timeseries"],
        })
    return out


def timeseries(plantation_id: str) -> list[dict]:
    rows = bq().query(
        f"SELECT date, ndvi_mean, ndvi_expected FROM `{DS}.plantation_timeseries` "
        "WHERE plantation_id = @pid ORDER BY date",
        job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("pid", "STRING", plantation_id)]),
    ).result()
    return [{"date": r.date.isoformat(), "ndvi": r.ndvi_mean, "expected": r.ndvi_expected} for r in rows]


def weather(points: list[tuple[str, float, float]], now: datetime | None = None) -> dict[str, dict]:
    """Rain forecast (next 10 days), rain observed (last 14 days) and root-zone soil moisture
    vs its 10-year normal for the same fortnight, for each (id, lon, lat). One EE round trip."""
    now = now or datetime.now(timezone.utc)
    t = ee.Date(now.strftime("%Y-%m-%dT%H:%M:%S"))

    gfs = ee.ImageCollection(GFS).filterDate(t.advance(-3, "day"), t.advance(1, "day"))
    run = gfs.filter(ee.Filter.eq("forecast_hours", 240)).aggregate_max("creation_time")
    # total_precipitation_surface accumulates from the start of the run.
    forecast = (gfs.filter(ee.Filter.eq("creation_time", run)).filter(ee.Filter.eq("forecast_hours", 240))
                .first().select("total_precipitation_surface").rename("fc10"))
    past = (ee.ImageCollection(GSMAP).filterDate(t.advance(-14, "day"), t)
            .select("hourlyPrecipRate").sum().rename("past14"))  # mm/h summed over hourly images = mm
    sm_now = ee.ImageCollection(SMAP).filterDate(t.advance(-6, "day"), t).select("sm_rootzone").mean().rename("sm")
    doy_start = t.advance(-10, "day")
    normal = ee.ImageCollection.fromImages(ee.List.sequence(1, 10).map(
        lambda k: ee.ImageCollection(SMAP).filterDate(doy_start.advance(ee.Number(k).multiply(-1), "year"),
                                                      t.advance(ee.Number(k).multiply(-1), "year"))
        .select("sm_rootzone").mean())).mean().rename("sm_normal")

    fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Point([lon, lat]), {"id": pid}) for pid, lon, lat in points])
    stats = ee.Image.cat([forecast, past, sm_now, normal]).reduceRegions(fc, ee.Reducer.first(), 9000)
    res = ee.Dictionary({"run": ee.Date(run).format("YYYY-MM-dd HH:mm 'UTC'"), "rows": stats.toList(100).map(
        lambda f: ee.Feature(f).toDictionary())}).getInfo()

    out = {}
    for row in res["rows"]:
        fc10, past14 = row.get("fc10"), row.get("past14")
        sm, sm_normal = row.get("sm"), row.get("sm_normal")
        dry = fc10 is not None and past14 is not None and fc10 < DRY_FORECAST_MM and past14 < DRY_PAST_MM
        out[row["id"]] = {
            "precip_next_10d_mm": round(fc10, 1) if fc10 is not None else None,
            "precip_last_14d_mm": round(past14, 1) if past14 is not None else None,
            "soil_moisture": round(sm, 3) if sm is not None else None,
            "soil_moisture_normal": round(sm_normal, 3) if sm_normal is not None else None,
            "soil_moisture_vs_normal_pct": round((sm / sm_normal - 1) * 100) if sm and sm_normal else None,
            "dry_spell": dry,
            "forecast_run": res["run"],
            "sources": WEATHER_SOURCES,
        }
    return out


def risk(p: dict, w: dict) -> dict:
    """Combine satellite health and weather into one actionable assessment.

    A dry 10-day forecast matters for plantations that are young (< 3 years) or already
    below target; established, on-track plantations ride it out, so they get no alert."""
    young = (p.get("age_years") or 0) < 3
    struggling = p["status"] in ("watch", "at_risk")
    dry_ahead = (w.get("precip_next_10d_mm") if w.get("precip_next_10d_mm") is not None else 99) < DRY_FORECAST_MM
    alerts = []
    if p["status"] == "at_risk":
        alerts.append({"type": "vegetation_decline", "severity": "critical"})
    elif p["status"] == "watch":
        alerts.append({"type": "vegetation_decline", "severity": "warning"})
    if dry_ahead and (young or struggling or w.get("dry_spell")):
        alerts.append({"type": "dry_spell", "severity": "critical" if (young or p["status"] == "at_risk") else "warning"})
    return {"alerts": alerts, "action_needed": bool(alerts), "dry_ahead": dry_ahead}


_weather_cache: dict[tuple, tuple[float, dict]] = {}
WEATHER_TTL_S = 30 * 60  # forecasts update every 6 h; soil moisture daily


def cached_weather(points: list[tuple[str, float, float]]) -> dict[str, dict]:
    key = tuple(sorted(points))
    hit = _weather_cache.get(key)
    if hit and time.time() - hit[0] < WEATHER_TTL_S:
        return hit[1]
    w = weather(points)
    _weather_cache[key] = (time.time(), w)
    return w


def check(plantation_id: str | None = None) -> list[dict]:
    every = plantations()
    ps = [p for p in every if plantation_id in (None, p["plantation_id"])]
    if not ps:
        return []
    # One weather call covers all plantations, so per-plantation checks hit the cache.
    w = cached_weather([(p["plantation_id"], *p["centroid"]) for p in every])
    return [{**p, "weather": w.get(p["plantation_id"], {}), **risk(p, w.get(p["plantation_id"], {}))} for p in ps]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def days_ago(n: int) -> datetime:
    return now_utc() - timedelta(days=n)
