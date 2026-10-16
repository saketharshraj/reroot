"""Replenishment verification: did a funded pond or lake project actually hold more water?

Data centres and other corporate water users claim "water positive" by funding lake revival,
desilting and recharge projects. This checks each claim from space:

- Lake outline: JRC Global Surface Water maximum extent (1984-2021) around the lake.
- Open water each month, 2018 onward: Sentinel-1 radar (VV backscatter < -16 dB) works through
  monsoon cloud. Radar misses water covered by weeds and can read smooth ground (runways, graded
  lake beds) as water, so measurement stays inside the lake outline and an optical cross-check
  rides along: Dynamic World water and Sentinel-2 dense green cover (NDVI > 0.5).
- Rain context: CHIRPS monsoon (Jun-Sep) rainfall, so a wet year can't pass for a working project.

Each hydrological year (Jul-Jun) gives a post-monsoon peak (Aug-Nov), dry-season retention
(Mar-Jun) and months holding water. Years before the intervention are the baseline; the
baseline peak is scaled by each later year's rainfall before comparing.
"""

import hashlib
import json
import time
from collections import defaultdict
from datetime import date

import ee
from google.cloud import bigquery

from reroot.api.alerts import _bucket
from reroot.core import config, layers as L
from reroot.core.clients import bq

DS = f"{config.PROJECT}.{config.BQ_DATASET}"
S1 = "COPERNICUS/S1_GRD"
JRC = "JRC/GSW1_4/GlobalSurfaceWater"
SOURCES = [S1, JRC, L.DYNAMIC_WORLD, L.S2, L.CLOUD_SCORE_PLUS, L.CHIRPS]
FIRST_YEAR = 2018
VV_WATER_DB = -16
GREEN_NDVI = 0.5
MIN_CLEAR = 0.6          # optical numbers only when >= 60% of the lake is cloud-free that month
DEFAULT_DEPTH_M = 1.5
RAIN_ELASTICITY = 0.5   # lake area responds less than proportionally to rain
SERIES_VERSION = "v1"

VERDICTS = {
    "verified": "More water than the rain alone explains, at the monsoon peak and into the dry season, in the latest year.",
    "partial": "Some gain after the project, but not both a higher peak and better dry-season retention in the latest year.",
    "at_risk": "Water came back after the project, but the surface is now mostly dense green cover: floating weeds such as "
               "water hyacinth, or a dried bed that has grown over. Satellites can't tell which; it needs a field check.",
    "not_additional": "The lake was already full before the project, so there is no extra water to credit.",
    "not_verified": "No change beyond what rainfall explains.",
    "too_early": "No monsoon has passed since the intervention yet.",
    "insufficient_data": "Not enough satellite history before the intervention to set a baseline.",
}

PROJECTS_TABLE = f"{DS}.water_projects"
SERIES_TABLE = f"{DS}.water_timeseries"


# ---------- Earth Engine ----------

def outline(lon: float, lat: float, search_m: float = 2000, max_dist_m: float = 400) -> dict | None:
    """The largest JRC max-extent water polygon within `max_dist_m` of the point, as GeoJSON."""
    pt = ee.Geometry.Point([lon, lat])
    mx = ee.Image(JRC).select("max_extent").selfMask()
    vec = mx.reduceToVectors(geometry=pt.buffer(search_m), scale=30, geometryType="polygon", maxPixels=1e8, bestEffort=True)
    vec = vec.map(lambda f: f.set("a", f.geometry().area(1), "d", f.geometry().distance(pt, 1)))
    near = vec.filter(ee.Filter.lt("d", max_dist_m)).sort("a", False)
    info = ee.Dictionary({"n": near.size(), "g": ee.Algorithms.If(
        near.size().gt(0), ee.Feature(near.first()).geometry().simplify(10), None)}).getInfo()
    return info["g"] if info["n"] else None


def _months(until: date | None = None) -> list[tuple[int, int]]:
    until = until or date.today()
    last = (until.year, until.month - 1) if until.month > 1 else (until.year - 1, 12)  # last full month
    return [(y, m) for y in range(FIRST_YEAR, last[0] + 1) for m in range(1, 13) if (y, m) <= last]


def _compute_series(geom: dict, months: list[tuple[int, int]]) -> list[dict]:
    g = ee.Geometry(geom)
    area = ee.Image.pixelArea()
    s1 = (ee.ImageCollection(S1).filterBounds(g).filter(ee.Filter.eq("instrumentMode", "IW"))
          .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV")).select("VV"))
    dw = ee.ImageCollection(L.DYNAMIC_WORLD).filterBounds(g).select("label")
    s2 = (ee.ImageCollection(L.S2).filterBounds(g).filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 70))
          .linkCollection(ee.ImageCollection(L.CLOUD_SCORE_PLUS), ["cs_cdf"])
          .map(lambda i: i.updateMask(i.select("cs_cdf").gte(0.6))))
    rain_area = g.centroid(10).buffer(5000)
    empty = ee.Image.constant(0).updateMask(ee.Image.constant(0))

    def one(y, m):
        d = ee.Date.fromYMD(y, m, 1)
        e = d.advance(1, "month")
        s1m, dwm, s2m = s1.filterDate(d, e), dw.filterDate(d, e), s2.filterDate(d, e)
        rad = ee.Image(ee.Algorithms.If(s1m.size().gt(0), s1m.median().focalMedian(20, "circle", "meters"), empty))
        lab = ee.Image(ee.Algorithms.If(dwm.size().gt(0), dwm.mode(), empty))
        nd = ee.Image(ee.Algorithms.If(s2m.size().gt(0), s2m.median().normalizedDifference(["B8", "B4"]), empty))
        img = ee.Image.cat(
            rad.lt(VV_WATER_DB).unmask(0).rename("w"), rad.gte(-100).unmask(0).rename("wc"),
            lab.eq(0).unmask(0).rename("ow"), lab.gte(0).unmask(0).rename("oc"),
            nd.gt(GREEN_NDVI).unmask(0).rename("gr"), nd.gte(-1).unmask(0).rename("gc"),
        ).multiply(area)
        sums = img.reduceRegion(ee.Reducer.sum(), g, 10, maxPixels=1e9)
        ch = ee.ImageCollection(L.CHIRPS).filterDate(d, e)
        rain = ee.Algorithms.If(ch.size().gt(0), ch.sum().reduceRegion(ee.Reducer.mean(), rain_area, 5000).get("precipitation"), None)
        return ee.Feature(None, sums).set("month", f"{y}-{m:02d}", "s1_n", s1m.size(), "rain_mm", rain)

    rows = ee.FeatureCollection([one(y, m) for y, m in months]).getInfo()["features"]
    total = g.area(1).getInfo()
    out = []
    for f in rows:
        p = f["properties"]
        def frac(k, c):
            return p.get(k, 0) / p[c] if p.get(c, 0) >= MIN_CLEAR * total else None
        ow, gr = frac("ow", "oc"), frac("gr", "gc")
        out.append({
            "month": p["month"],
            "water_ha": round(p.get("w", 0) / 1e4, 2) if p.get("wc", 0) > 0 else None,
            "optical_water_ha": round(ow * total / 1e4, 2) if ow is not None else None,
            "green_ha": round(gr * total / 1e4, 2) if gr is not None else None,
            "rain_mm": round(p["rain_mm"], 1) if p.get("rain_mm") is not None else None,
            "s1_images": p.get("s1_n", 0),
        })
    return out


def series(geom: dict) -> list[dict]:
    """Monthly series for a lake outline, cached in Cloud Storage per outline and last month."""
    months = _months()
    key = hashlib.sha1(json.dumps([SERIES_VERSION, geom, months[-1]]).encode()).hexdigest()[:16]
    blob = _bucket().blob(f"water/series/{key}.json")
    if blob.exists():
        return json.loads(blob.download_as_text())
    s = _compute_series(geom, months)
    blob.upload_from_string(json.dumps(s), content_type="application/json")
    return s


def area_ha(geom: dict) -> float:
    return round(ee.Geometry(geom).area(1).getInfo() / 1e4, 1)


# ---------- metrics (pure Python) ----------

def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def hydro_years(rows: list[dict], outline_ha: float) -> list[dict]:
    """One row per hydrological year Y (Jul Y - Jun Y+1)."""
    by = {r["month"]: r for r in rows}
    wet_thr = max(0.2, 0.1 * outline_ha)
    years = sorted({int(m[:4]) for m in by})
    out = []
    for y in years:
        def get(yy, mm, k):
            r = by.get(f"{yy}-{mm:02d}")
            return r.get(k) if r else None
        peak_vals = [get(y, m, "water_ha") for m in (8, 9, 10, 11)]
        dry_vals = [get(y + 1, m, "water_ha") for m in (3, 4, 5, 6)]
        year_vals = [get(y, m, "water_ha") for m in range(7, 13)] + [get(y + 1, m, "water_ha") for m in range(1, 7)]
        rain_vals = [get(y, m, "rain_mm") for m in (6, 7, 8, 9)]
        green_dry = [get(y + 1, m, "green_ha") for m in (3, 4, 5, 6)]
        n_peak = sum(v is not None for v in peak_vals)
        if n_peak < 2:
            continue
        n_dry = sum(v is not None for v in dry_vals)
        out.append({
            "year": y, "label": f"{y}-{str(y + 1)[2:]}",
            "peak_ha": round(max(v for v in peak_vals if v is not None), 2),
            "dry_ha": round(_mean(dry_vals), 2) if n_dry >= 2 else None,
            "wet_months": sum(1 for v in year_vals if v is not None and v >= wet_thr),
            "months_observed": sum(v is not None for v in year_vals),
            "monsoon_rain_mm": round(sum(rain_vals), 0) if all(v is not None for v in rain_vals) else None,
            "green_dry_share": round(_mean(green_dry) / outline_ha, 2) if _mean(green_dry) is not None and outline_ha else None,
            "complete": n_dry >= 2,
        })
    return out


def _material(gain: float | None, base: float | None, outline_ha: float) -> bool:
    if gain is None:
        return False
    return gain >= max(0.2, 0.1 * outline_ha) and gain >= 0.2 * (base or 0)


def evaluate(rows: list[dict], outline_ha: float, intervention: str, depth_m: float = DEFAULT_DEPTH_M,
             claimed_m3: float | None = None) -> dict:
    """Compare hydrological years before and after `intervention` (YYYY-MM)."""
    iy, im = int(intervention[:4]), int(intervention[5:7])
    hy = hydro_years(rows, outline_ha)
    # baseline: years that ended (June) by the intervention; post: years that began (July) after it
    base = [h for h in hy if (h["year"] + 1, 6) <= (iy, im)][-3:]
    post = [h for h in hy if (h["year"], 7) >= (iy, im)]
    res = {"intervention": intervention, "outline_ha": outline_ha, "years": hy,
           "baseline_years": [h["label"] for h in base], "post_years": [h["label"] for h in post],
           "depth_m": depth_m, "claimed_m3_yr": claimed_m3}
    if not base:
        return res | {"verdict": "insufficient_data", "verdict_text": VERDICTS["insufficient_data"], "credited_m3_yr": 0}
    if not post:
        return res | {"verdict": "too_early", "verdict_text": VERDICTS["too_early"], "credited_m3_yr": 0}

    b_peak = _mean([h["peak_ha"] for h in base])
    b_max = max(h["peak_ha"] for h in base)
    b_dry = _mean([h["dry_ha"] for h in base])
    b_rain = _mean([h["monsoon_rain_mm"] for h in base])
    b_wet = _mean([h["wet_months"] for h in base])
    for h in post:
        ratio = (h["monsoon_rain_mm"] / b_rain) if (h["monsoon_rain_mm"] and b_rain) else 1.0
        ratio = max(0.5, min(ratio, 2.0))
        expected = b_peak * ratio ** RAIN_ELASTICITY
        if ratio > 1:
            expected = min(expected, max(b_max, b_peak))  # a wetter year can't fill the lake past what it held before
        h["rain_vs_baseline"] = round(ratio, 2)
        h["expected_peak_ha"] = round(expected, 2)
        h["excess_peak_ha"] = round(h["peak_ha"] - expected, 2)
        h["dry_gain_ha"] = round(h["dry_ha"] - b_dry, 2) if h["dry_ha"] is not None and b_dry is not None else None
        h["peak_gain"] = _material(h["excess_peak_ha"], b_peak, outline_ha)
        h["dry_gain"] = _material(h["dry_gain_ha"], b_dry, outline_ha)

    latest_full = next((h for h in reversed(post) if h["complete"]), None)
    judged = latest_full or post[-1]
    gain_years = [h["label"] for h in post if h["peak_gain"] or h["dry_gain"]]
    recent_water = _mean([r["water_ha"] for r in rows[-6:]])
    recent_green = _mean([r["green_ha"] for r in rows[-6:]])
    best_peak = max(h["peak_ha"] for h in post)
    # permanent before the project: water all year and the dry season held nearly the monsoon peak
    permanent_before = b_wet >= 11 and b_peak > 0 and (b_dry or 0) >= 0.8 * b_peak

    if gain_years and recent_green is not None and recent_green >= 0.4 * outline_ha and (recent_water or 0) < 0.25 * best_peak:
        verdict = "at_risk"
    elif permanent_before and not judged["peak_gain"]:
        verdict = "not_additional"
    elif judged["peak_gain"] and judged["dry_gain"]:
        verdict = "verified"
    elif judged["peak_gain"] or judged["dry_gain"] or len(gain_years) * 2 >= len(post):
        verdict = "partial"
    else:
        verdict = "not_verified"
    earlier = [h["label"] for h in post if (h["peak_gain"] or h["dry_gain"]) and h["year"] < judged["year"]]
    note = f"A gain in {', '.join(earlier)} did not last into {judged['label']}." if verdict in ("not_verified", "not_additional") and earlier else None

    credit_ha = max(0.0, judged["excess_peak_ha"]) if verdict in ("verified", "partial") else 0.0
    credited = credit_ha * 1e4 * depth_m
    if claimed_m3:
        credited = min(credited, claimed_m3)
    agree = [(r["water_ha"] >= 0.2) == (r["optical_water_ha"] >= 0.2) for r in rows
             if r["water_ha"] is not None and r["optical_water_ha"] is not None]
    return res | {
        "verdict": verdict, "verdict_text": VERDICTS[verdict], "note": note, "gain_years": gain_years,
        "baseline": {"peak_ha": round(b_peak, 2), "max_peak_ha": round(b_max, 2), "dry_ha": round(b_dry, 2) if b_dry is not None else None,
                     "monsoon_rain_mm": round(b_rain) if b_rain else None, "wet_months": round(b_wet, 1)},
        "judged_year": judged["label"],
        "recent": {"months": [rows[-6]["month"], rows[-1]["month"]] if len(rows) >= 6 else None,
                   "open_water_ha": round(recent_water, 2) if recent_water is not None else None,
                   "green_cover_ha": round(recent_green, 2) if recent_green is not None else None},
        "credited_m3_yr": round(credited, -2),
        "radar_optical_agreement": round(sum(agree) / len(agree), 2) if agree else None,
        "assumptions": [
            f"Credited volume = extra post-monsoon water area beyond the rain-scaled baseline x {depth_m} m depth, "
            "assuming one fill per monsoon (indicative; depth isn't visible from space).",
            "Expected peak = baseline peak x (Jun-Sep rain / baseline rain)^0.5, never above the largest pre-project peak "
            "in a wetter year (lakes saturate; rain ratio capped 0.5-2x).",
            "A gain counts when it is at least 10% of the lake's area (min 0.2 ha) and 20% of the baseline.",
            "Radar: Sentinel-1 VV < -16 dB = open water; ponds under ~2 ha are near the 10 m resolution limit.",
        ],
    }


# ---------- registry and ledger ----------

_cache: dict = {}
CACHE_S = 600


def _registry() -> dict:
    hit = _cache.get("reg")
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    projects = {}
    for r in bq().query(f"SELECT * FROM `{PROJECTS_TABLE}` ORDER BY project_id").result():
        projects[r.project_id] = dict(r.items()) | {"geometry": json.loads(r.geojson)}
    rows = defaultdict(list)
    for r in bq().query(f"SELECT * FROM `{SERIES_TABLE}` ORDER BY project_id, month").result():
        rows[r.project_id].append({"month": r.month.strftime("%Y-%m"), "water_ha": r.water_ha,
                                   "optical_water_ha": r.optical_water_ha, "green_ha": r.green_ha,
                                   "rain_mm": r.rain_mm, "s1_images": r.s1_images})
    out = {"projects": projects, "series": dict(rows)}
    _cache["reg"] = (time.time(), out)
    return out


def _brief(p: dict) -> dict:
    return {k: p.get(k) for k in ("project_id", "name", "place", "sponsor", "intervention", "intervention_date",
                                  "outline_ha", "depth_m", "claimed_m3_yr", "is_demo", "notes", "references", "lon", "lat")}


def match(name: str) -> str | None:
    """Registered project whose name shares a distinctive word with `name` (e.g. "Damdama lake" -> W3)."""
    generic = {"lake", "jheel", "pond", "talab", "wetland", "the", "revival", "restoration", "desilting", "project"}
    words = {w for w in name.lower().replace(",", " ").split() if w not in generic and len(w) > 2}
    for pid, p in _registry()["projects"].items():
        if words & set(p["name"].lower().split()):
            return pid
    return None


def project_series(project_id: str) -> list[dict]:
    return _registry()["series"].get(project_id, [])


def verify(project_id: str) -> dict | None:
    reg = _registry()
    p = reg["projects"].get(project_id)
    if not p:
        return None
    ev = evaluate(reg["series"].get(project_id, []), p["outline_ha"], p["intervention_date"], p["depth_m"], p["claimed_m3_yr"])
    return _brief(p) | {"geometry": p["geometry"], "evaluation": ev, "series": reg["series"].get(project_id, []),
                        "sources": SOURCES + [PROJECTS_TABLE, SERIES_TABLE]}


def evidence(ev: dict) -> str | None:
    """One line of the numbers behind a verdict, for the agent to quote."""
    h = next((y for y in ev.get("years", []) if y["label"] == ev.get("judged_year")), None)
    if not h or "baseline" not in ev:
        return None
    b, r = ev["baseline"], ev.get("recent") or {}
    parts = [f"{h['label']}: post-monsoon peak {h['peak_ha']} ha open water vs {h.get('expected_peak_ha')} ha expected from rain "
             f"(baseline {b['peak_ha']} ha)"]
    if h.get("dry_ha") is not None and b.get("dry_ha") is not None:
        parts.append(f"dry season {h['dry_ha']} ha vs {b['dry_ha']} ha baseline")
    if r.get("open_water_ha") is not None:
        parts.append(f"last 6 months {r['open_water_ha']} ha open water (radar)"
                     + (f", {r['green_cover_ha']} ha dense green cover (optical)" if r.get("green_cover_ha") is not None else ""))
    return "; ".join(parts) + f"; lake outline {ev['outline_ha']} ha"


def projects() -> list[dict]:
    """Every registered project with its verdict (no monthly series)."""
    out = []
    for pid in _registry()["projects"]:
        v = verify(pid)
        ev = v["evaluation"]
        out.append(_brief(v) | {"geometry": v["geometry"], "verdict": ev["verdict"], "credited_m3_yr": ev["credited_m3_yr"],
                                "judged_year": ev.get("judged_year"), "recent": ev.get("recent"), "evidence": evidence(ev)})
    return out


def ledger(target_m3_yr: float | None = None, sponsor: str | None = None, target_source: str | None = None) -> dict:
    """Claimed vs satellite-verified replenishment for a sponsor's portfolio."""
    ps = [p for p in projects() if not sponsor or p["sponsor"] == sponsor]
    if not ps:
        return {"error": "no water projects registered"}
    sponsor = sponsor or ps[0]["sponsor"]
    reg = _registry()["projects"]
    stored = next((reg[p["project_id"]].get("sponsor_target_m3_yr") for p in ps if reg[p["project_id"]].get("sponsor_target_m3_yr")), None)
    target = target_m3_yr or stored
    claimed = sum(p["claimed_m3_yr"] or 0 for p in ps)
    credited = sum(p["credited_m3_yr"] or 0 for p in ps)
    pct = (lambda x: round(100 * x / target) if target else None)
    return {
        "sponsor": sponsor, "target_m3_yr": target,
        "target_source": target_source or ("sponsor record: 120% of the site's freshwater use" if stored else None),
        "claimed_m3_yr": claimed, "verified_m3_yr": credited,
        "claimed_pct_of_target": pct(claimed), "verified_pct_of_target": pct(credited),
        "gap_m3_yr": max(0, (target or 0) - credited),
        "projects": [{k: p[k] for k in ("project_id", "name", "verdict", "claimed_m3_yr", "credited_m3_yr", "judged_year", "evidence")} for p in ps],
        "sources": SOURCES + [PROJECTS_TABLE, SERIES_TABLE],
    }


def verify_adhoc(lon: float, lat: float, intervention: str, name: str | None = None, depth_m: float = DEFAULT_DEPTH_M,
                 claimed_m3: float | None = None) -> dict:
    """Verify any lake or pond: outline from JRC, live monthly series (cached), same evaluation."""
    geom = outline(lon, lat)
    if not geom:
        return {"error": "no mapped water body within 400 m of that point (JRC Global Surface Water, 1984-2021)"}
    oha = area_ha(geom)
    rows = series(geom)
    return {"project_id": None, "name": name or "Water body", "lon": lon, "lat": lat, "outline_ha": oha,
            "intervention_date": intervention, "depth_m": depth_m, "claimed_m3_yr": claimed_m3, "is_demo": False,
            "geometry": geom, "evaluation": evaluate(rows, oha, intervention, depth_m, claimed_m3), "series": rows,
            "sources": SOURCES}


def invalidate() -> None:
    _cache.clear()


SCHEMA = {
    PROJECTS_TABLE: [
        bigquery.SchemaField("project_id", "STRING", "REQUIRED"), bigquery.SchemaField("name", "STRING"),
        bigquery.SchemaField("place", "STRING"), bigquery.SchemaField("lon", "FLOAT64"), bigquery.SchemaField("lat", "FLOAT64"),
        bigquery.SchemaField("geojson", "STRING"), bigquery.SchemaField("outline_ha", "FLOAT64"),
        bigquery.SchemaField("sponsor", "STRING"), bigquery.SchemaField("sponsor_target_m3_yr", "FLOAT64"),
        bigquery.SchemaField("intervention", "STRING"), bigquery.SchemaField("intervention_date", "STRING"),
        bigquery.SchemaField("depth_m", "FLOAT64"), bigquery.SchemaField("claimed_m3_yr", "FLOAT64"),
        bigquery.SchemaField("notes", "STRING"), bigquery.SchemaField("references", "STRING", "REPEATED"),
        bigquery.SchemaField("is_demo", "BOOL"),
    ],
    SERIES_TABLE: [
        bigquery.SchemaField("project_id", "STRING", "REQUIRED"), bigquery.SchemaField("month", "DATE"),
        bigquery.SchemaField("water_ha", "FLOAT64"), bigquery.SchemaField("optical_water_ha", "FLOAT64"),
        bigquery.SchemaField("green_ha", "FLOAT64"), bigquery.SchemaField("rain_mm", "FLOAT64"),
        bigquery.SchemaField("s1_images", "INT64"),
    ],
}
