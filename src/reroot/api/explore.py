"""Exploration tools for the agent: find a place, analyse the land around it, make a satellite
timelapse of any target. All results are things the map can show."""

import hashlib
import io
import json
import math
import re
from functools import lru_cache

import ee
import requests
from google.cloud import bigquery
from PIL import Image, ImageDraw, ImageFont, ImageSequence

from reroot.api import assess as assess_mod
from reroot.api.alerts import _bucket
from reroot.core import config, layers as L
from reroot.core.clients import bq

DS = f"{config.PROJECT}.{config.BQ_DATASET}"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "ReRoot/0.3 (hackathon demo; github.com/saketharshraj/reroot)"


GENERIC = re.compile(r"\b(village|town|city|gaon|block|area|near|the|district)\b", re.I)


SETTLEMENTS = {"village", "hamlet", "town", "city", "suburb", "neighbourhood", "locality", "isolated_dwelling"}


def find_place(query: str, region: str = config.DEFAULT_REGION) -> list[dict]:
    """Search the query as typed and without generic words (OSM names villages without the word
    "village"), merge, and rank settlements first."""
    simpler = " ".join(GENERIC.sub(" ", query).split())
    hits = _nominatim(query, region) + (_nominatim(simpler, region) if simpler and simpler != query else [])
    seen, out = set(), []
    for h in sorted(hits, key=lambda h: (0 if h["kind"] in SETTLEMENTS else 1, -h["importance"])):
        key = (h["name"], round(h["lon"], 3), round(h["lat"], 3))
        if key not in seen:
            seen.add(key)
            out.append(h)
    # If the best match is a business inside the place the user named (e.g. a guesthouse called
    # "The Crashpad Mangar" for "Mangar village"), label it with the place name.
    if out and out[0]["kind"] not in SETTLEMENTS and simpler and simpler.lower() in out[0]["name"].lower():
        out[0] = {**out[0], "name": simpler.title(), "label": f"{simpler.title()} (near {out[0]['name']})"}
    return out


@lru_cache(maxsize=256)
def _nominatim(query: str, region: str) -> list[dict]:
    """Geocode inside the analysed region only (OpenStreetMap Nominatim, cached; < 1 req/s)."""
    w, s, e, n = config.REGIONS[region]["bbox"]
    r = requests.get(NOMINATIM, params={"q": query, "format": "jsonv2", "limit": 8, "countrycodes": "in",
                                         "viewbox": f"{w},{n},{e},{s}", "bounded": 1},
                     headers={"User-Agent": UA}, timeout=10)
    r.raise_for_status()
    return [{"name": x.get("name") or x["display_name"].split(",")[0], "label": x["display_name"],
             "lon": float(x["lon"]), "lat": float(x["lat"]),
             "kind": "village" if x.get("category") == "place" and x.get("addresstype") not in SETTLEMENTS else (x.get("addresstype") or x.get("type")),
             "importance": float(x.get("importance") or 0)} for x in r.json()]


def circle(lon: float, lat: float, radius_km: float, n: int = 64) -> dict:
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * math.cos(math.radians(lat)))
    ring = [[round(lon + dlon * math.cos(2 * math.pi * i / n), 6), round(lat + dlat * math.sin(2 * math.pi * i / n), 6)]
            for i in range(n)]
    return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}


ELIGIBLE = ("frac_trees <= 0.5 AND frac_built <= 0.2 AND frac_water <= 0.2 AND frac_crops <= 0.4 "
            "AND frac_grass <= 0.4 AND slope_deg <= 20")


def analyze(lon: float, lat: float, radius_km: float, name: str | None = None) -> dict:
    geom = circle(lon, lat, radius_km)
    a = assess_mod.assess(geom)
    row = list(bq().query(f"""
WITH q AS (  -- top-quartile survival odds among plantable land in the whole region
  SELECT APPROX_QUANTILES(p.ml_score, 4)[OFFSET(3)] AS q75
  FROM `{DS}.grid_cells` c JOIN `{DS}.cell_predictions` p USING (cell_id)
  WHERE c.region = @region AND {ELIGIBLE.replace("frac_", "c.frac_").replace("slope_deg", "c.slope_deg")})
SELECT COUNT(*) AS cells, COUNTIF({ELIGIBLE}) AS plantable,
       COUNTIF({ELIGIBLE} AND p.ml_score >= q.q75) AS plantable_top,
       AVG(c.ndvi_dry) AS ndvi, AVG(c.pop_1km) AS pop
FROM `{DS}.grid_cells` c JOIN `{DS}.cell_predictions` p USING (cell_id), q
WHERE c.region = @region AND ST_DWITHIN(c.centroid, ST_GEOGPOINT(@lon, @lat), @r)""",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("region", "STRING", config.DEFAULT_REGION),
            bigquery.ScalarQueryParameter("lon", "FLOAT64", lon),
            bigquery.ScalarQueryParameter("lat", "FLOAT64", lat),
            bigquery.ScalarQueryParameter("r", "FLOAT64", radius_km * 1000)])).result())[0]
    total = sum(a["land_cover_ha"].values()) or 1
    return {
        "name": name, "center": [lon, lat], "radius_km": radius_km, "geometry": geom,
        "area_ha": a["area_ha"],
        "land_cover_pct": {k: round(v / total * 100, 1) for k, v in sorted(a["land_cover_ha"].items(), key=lambda kv: -kv[1])},
        "tree_cover_ha": a["tree_cover_ha"], "co2e_t_est": a["co2e_t_est"], "canopy_height_mean_m": a["canopy_height_mean_m"],
        "plantable_ha": int(row.plantable or 0),
        # hectares whose survival odds are in the region's top 25% (a rank, not a probability)
        "plantable_top_quartile_survival_ha": int(row.plantable_top or 0),
        "ndvi_dry_mean": round(row.ndvi, 3) if row.ndvi is not None else None,
        "sources": a["sources"] + [f"{DS}.grid_cells", f"{DS}.cell_predictions"],
    }


def _bounds(geoms: list[dict], pad_m: float = 300) -> list[float]:
    pts = []
    for g in geoms:
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        pts += [pt for p in polys for pt in p[0]]
    pad = pad_m / 111_000
    return [min(p[0] for p in pts) - pad, min(p[1] for p in pts) - pad, max(p[0] for p in pts) + pad, max(p[1] for p in pts) + pad]


def timelapse(geoms: list[dict], title: str, start_year: int = 2019, end_year: int = 2026) -> dict:
    """Dry-season (Jan-Apr) Sentinel-2 frames per year, year-stamped, outline drawn. Cached in GCS."""
    start_year, end_year = max(2019, int(start_year)), min(2026, int(end_year))
    bbox = _bounds(geoms)
    key = hashlib.sha1(json.dumps([bbox, start_year, end_year, geoms]).encode()).hexdigest()[:16]
    blob = _bucket().blob(f"media/{key}.gif")
    if blob.exists():
        return {"url": f"/v1/media/{key}.gif", "title": title, "years": [start_year, end_year], "bbox": bbox}

    region = L.bbox(bbox)
    outline = ee.Image().byte().paint(ee.FeatureCollection([ee.Feature(ee.Geometry(g)) for g in geoms]), 1, 2)
    years = list(range(start_year, end_year + 1))
    frames = ee.ImageCollection([
        L.s2_composite(region, f"{y}-01-01", f"{y}-05-01").visualize(**L.RGB_VIS)
        .blend(outline.visualize(palette=["ffff00"])) for y in years])
    url = frames.getVideoThumbURL({"region": region, "dimensions": 520, "framesPerSecond": 1, "crs": "EPSG:3857"})
    raw = requests.get(url, timeout=300)
    raw.raise_for_status()
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 26)
    except OSError:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 26)
        except OSError:
            font = ImageFont.load_default()
    out_frames = []
    for year, frame in zip(years, ImageSequence.Iterator(Image.open(io.BytesIO(raw.content)))):
        f = frame.convert("RGB")
        d = ImageDraw.Draw(f)
        d.rectangle([8, 8, 92, 46], fill=(0, 0, 0))
        d.text((16, 12), str(year), fill=(255, 255, 255), font=font)
        out_frames.append(f)
    buf = io.BytesIO()
    out_frames[0].save(buf, format="GIF", save_all=True, append_images=out_frames[1:], duration=900, loop=0)
    blob.upload_from_string(buf.getvalue(), content_type="image/gif")
    return {"url": f"/v1/media/{key}.gif", "title": title, "years": [start_year, end_year], "bbox": bbox}
