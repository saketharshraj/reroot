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
from reroot.core import config, layers as L, regions
from reroot.core.clients import bq

DS = f"{config.PROJECT}.{config.BQ_DATASET}"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "ReRoot/0.3 (hackathon demo; github.com/saketharshraj/reroot)"


GENERIC = re.compile(r"\b(village|town|city|gaon|block|area|near|the|district)\b", re.I)


SETTLEMENTS = {"village", "hamlet", "town", "city", "suburb", "neighbourhood", "locality", "isolated_dwelling"}


def find_place(query: str) -> list[dict]:
    """Search India-wide (biased toward the demo region), both as typed and without generic words
    (OSM names villages without the word "village"). Each hit says whether its region's data is
    loaded; places in loaded regions and settlements rank first."""
    simpler = " ".join(GENERIC.sub(" ", query).split())
    hits = _nominatim(query) + (_nominatim(simpler) if simpler and simpler != query else [])
    for h in hits:
        rid = regions.containing(h["lon"], h["lat"], ready_only=False)
        h["region_id"] = rid
        h["data_loaded"] = bool(rid) and regions.spec(rid)["status"] == "ready"
    seen, out = set(), []
    for h in sorted(hits, key=lambda h: (0 if h["data_loaded"] else 1, 0 if h["kind"] in SETTLEMENTS else 1, -h["importance"])):
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
def _nominatim(query: str) -> list[dict]:
    """OpenStreetMap Nominatim, India only, biased to the demo region (cached; < 1 req/s)."""
    w, s, e, n = config.REGIONS[config.DEFAULT_REGION]["bbox"]
    r = requests.get(NOMINATIM, params={"q": query, "format": "jsonv2", "limit": 8, "countrycodes": "in",
                                         "viewbox": f"{w},{n},{e},{s}", "bounded": 0},
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
    total = sum(a["land_cover_ha"].values()) or 1
    base = {
        "name": name, "center": [lon, lat], "radius_km": radius_km, "geometry": geom, "area_ha": a["area_ha"],
        "land_cover_pct": {k: round(v / total * 100, 1) for k, v in sorted(a["land_cover_ha"].items(), key=lambda kv: -kv[1])},
        "tree_cover_ha": a["tree_cover_ha"], "co2e_t_est": a["co2e_t_est"], "canopy_height_mean_m": a["canopy_height_mean_m"],
    }
    region_id = regions.containing(lon, lat)
    if not region_id:  # satellite numbers work anywhere; planting potential needs the 1-ha grid
        return base | {"plantable_ha": None, "plantable_top_quartile_survival_ha": None, "ndvi_dry_mean": None,
                       "region_loaded": False, "sources": a["sources"],
                       "note": "Planting potential needs this region's 1-hectare grid. Offer load_region (about 10 minutes)."}
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
            bigquery.ScalarQueryParameter("region", "STRING", region_id),
            bigquery.ScalarQueryParameter("lon", "FLOAT64", lon),
            bigquery.ScalarQueryParameter("lat", "FLOAT64", lat),
            bigquery.ScalarQueryParameter("r", "FLOAT64", radius_km * 1000)])).result())[0]
    return base | {
        "plantable_ha": int(row.plantable or 0),
        # hectares whose survival odds are in the region's top 25% (a rank, not a probability)
        "plantable_top_quartile_survival_ha": int(row.plantable_top or 0),
        "ndvi_dry_mean": round(row.ndvi, 3) if row.ndvi is not None else None,
        "region_loaded": True, "region_id": region_id,
        "sources": a["sources"] + [f"{DS}.grid_cells", f"{DS}.cell_predictions"],
    }


def _bounds(geoms: list[dict], pad_m: float = 300) -> list[float]:
    pts = []
    for g in geoms:
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        pts += [pt for p in polys for pt in p[0]]
    pad = pad_m / 111_000
    return [min(p[0] for p in pts) - pad, min(p[1] for p in pts) - pad, max(p[0] for p in pts) + pad, max(p[1] for p in pts) + pad]


ZOOM_PAD_M = {"closer": 40, "normal": 300, "wider": 1500}
SEASONS = {"dry": ("01-01", "05-01", "Jan–Apr"), "post_monsoon": ("10-01", "12-31", "Oct–Dec")}
MAX_PX = 1024


def timelapse(geoms: list[dict], title: str, start_year: int = 2019, end_year: int = 2026, zoom: str = "normal",
              size_px: int = 520, layer: str = "true_colour", season: str = "dry") -> dict:
    """One Sentinel-2 frame per year (10 m pixels), year-stamped, outline drawn. Cached in GCS.
    zoom: closer | normal | wider (padding around the target). layer: true_colour | ndvi."""
    start_year, end_year = max(2019, int(start_year)), min(2026, int(end_year))
    zoom = zoom if zoom in ZOOM_PAD_M else "normal"
    season = season if season in SEASONS else "dry"
    layer = layer if layer in ("true_colour", "ndvi") else "true_colour"
    size_px = max(256, min(int(size_px or 520), MAX_PX))
    bbox = _bounds(geoms, ZOOM_PAD_M[zoom])
    width_m = (bbox[2] - bbox[0]) * 111_320 * math.cos(math.radians((bbox[1] + bbox[3]) / 2))
    key = hashlib.sha1(json.dumps([bbox, start_year, end_year, geoms, size_px, layer, season]).encode()).hexdigest()[:16]
    meta = {"url": f"/v1/media/{key}.gif", "title": title, "years": [start_year, end_year], "bbox": bbox,
            "zoom": zoom, "size_px": size_px, "layer": layer, "season": SEASONS[season][2],
            "metres_per_pixel": round(width_m / size_px, 1), "native_resolution_m": 10}
    blob = _bucket().blob(f"media/{key}.gif")
    if blob.exists():
        return meta

    region = L.bbox(bbox)
    outline = ee.Image().byte().paint(ee.FeatureCollection([ee.Feature(ee.Geometry(g)) for g in geoms]), 1, 2)
    years = list(range(start_year, end_year + 1))
    a, b, _ = SEASONS[season]

    def frame(y):
        comp = L.s2_composite(region, f"{y}-{a}", f"{y}-{b}")
        vis = L.ndvi(comp).visualize(**L.NDVI_VIS) if layer == "ndvi" else comp.visualize(**L.RGB_VIS)
        return vis.blend(outline.visualize(palette=["ffff00"]))

    frames = ee.ImageCollection([frame(y) for y in years])
    url = frames.getVideoThumbURL({"region": region, "dimensions": size_px, "framesPerSecond": 1, "crs": "EPSG:3857"})
    raw = requests.get(url, timeout=300)
    raw.raise_for_status()
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", max(18, size_px // 20))
    except OSError:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", max(18, size_px // 20))
        except OSError:
            font = ImageFont.load_default()
    out_frames = []
    for year, fr in zip(years, ImageSequence.Iterator(Image.open(io.BytesIO(raw.content)))):
        f = fr.convert("RGB")
        d = ImageDraw.Draw(f)
        d.rectangle([8, 8, 8 + size_px // 6, 8 + size_px // 14], fill=(0, 0, 0))
        d.text((16, 10), str(year), fill=(255, 255, 255), font=font)
        out_frames.append(f)
    buf = io.BytesIO()
    out_frames[0].save(buf, format="GIF", save_all=True, append_images=out_frames[1:], duration=900, loop=0)
    blob.upload_from_string(buf.getvalue(), content_type="image/gif")
    return meta
