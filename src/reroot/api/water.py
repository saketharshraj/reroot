"""Replenishment verification: did a funded pond or lake project actually hold more water?

Data centres and other corporate water users claim "water positive" by funding lake revival,
desilting and recharge projects. This checks each claim from space:

- Lake outline: JRC Global Surface Water maximum extent (1984-2021) around the lake.
- Open water each month, 2018 onward: Sentinel-1 radar (VV backscatter < -16 dB) works through
  monsoon cloud. Radar misses water covered by weeds and can read smooth ground (runways, graded
  lake beds) as water, so measurement stays inside the lake outline and an optical cross-check
  rides along: Dynamic World water and Sentinel-2 dense green cover (NDVI > 0.5).
- Rain context: rainy-season rainfall (CHIRPS; ERA5-Land beyond 50 degrees latitude), so a wet year
  can't pass for a working project.
- Automatic seasons: each lake's rainy season is the wettest 4 months of its 1991-2020 rainfall
  climatology. The water year starts the month after the rains begin, the lake is judged fullest
  in the 4 months from the second rainy month, and dry-season retention in the 4 months up to the
  next rains. Delhi gets Jun-Sep rains, Aug-Nov peak, Mar-Jun dry; Chennai, Bengaluru or Cape
  Town get their own. Where rain is spread evenly, calendar years with the 4 driest months.

Each water year gives a peak, dry-season retention and months holding water. Years before the
intervention are the baseline; the baseline peak is scaled by each later year's rainfall.
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
CHIRPS_PENTAD = "UCSB-CHG/CHIRPS/PENTAD"
ERA5_MONTHLY = "ECMWF/ERA5_LAND/MONTHLY_AGGR"
CHIRPS_MAX_LAT = 50      # CHIRPS covers 50S-50N; ERA5-Land beyond
CLIMATE_YEARS = (1991, 2020)
WEAK_SHARE = 0.45        # wettest 4 months under 45% of the year's rain (an even spread is 33%): no clear rainy season
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
# Used only when a lake has no climatology yet: Delhi-like monsoon, which reproduces Jun-Sep rains.
DEFAULT_CLIMATOLOGY = [15, 18, 13, 9, 20, 70, 200, 230, 120, 15, 3, 8]
FIRST_YEAR = 2018
VV_WATER_DB = -16
GREEN_NDVI = 0.5
MIN_CLEAR = 0.6          # optical numbers only when >= 60% of the lake is cloud-free that month
DEFAULT_DEPTH_M = 1.5
RAIN_ELASTICITY = 0.5   # lake area responds less than proportionally to rain
SERIES_VERSION = "v1"

VERDICTS = {
    "verified": "More water than the rain alone explains, at the seasonal peak and into the dry season, in the latest year.",
    "partial": "Some gain after the project, but not both a higher peak and better dry-season retention in the latest year.",
    "at_risk": "Water came back after the project, but the surface is now mostly dense green cover: floating weeds such as "
               "water hyacinth, or a dried bed that has grown over. Satellites can't tell which; it needs a field check.",
    "not_additional": "The lake was already full before the project, so there is no extra water to credit.",
    "not_verified": "No change beyond what rainfall explains.",
    "too_early": "No rainy season has passed since the intervention yet.",
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


def _rain_source(lat: float) -> tuple[ee.ImageCollection, str, float, float, str]:
    """(collection, band, scale m, factor to mm, id) for monthly rain at this latitude."""
    if abs(lat) < CHIRPS_MAX_LAT:
        return ee.ImageCollection(L.CHIRPS), "precipitation", 5566, 1.0, L.CHIRPS
    return ee.ImageCollection(ERA5_MONTHLY), "total_precipitation_sum", 11132, 1000.0, ERA5_MONTHLY


def climatology(lon: float, lat: float) -> dict:
    """Mean rainfall per calendar month (mm), 1991-2020, around the point. Cached in Cloud Storage."""
    blob = _bucket().blob(f"water/climate/{lon:.2f}_{lat:.2f}.json")
    if blob.exists():
        return json.loads(blob.download_as_text())
    y0, y1 = CLIMATE_YEARS
    if abs(lat) < CHIRPS_MAX_LAT:
        col, band, scale, factor, src = ee.ImageCollection(CHIRPS_PENTAD), "precipitation", 5566, 1.0, CHIRPS_PENTAD
    else:
        col, band, scale, factor, src = _rain_source(lat)
    col = col.filterDate(f"{y0}-01-01", f"{y1 + 1}-01-01").select(band)
    area = ee.Geometry.Point([lon, lat]).buffer(scale * 1.5)
    vals = ee.List([col.filter(ee.Filter.calendarRange(m, m, "month")).sum()
                    .reduceRegion(ee.Reducer.mean(), area, scale).get(band) for m in range(1, 13)]).getInfo()
    out = {"mm": [round((v or 0) * factor / (y1 - y0 + 1), 1) for v in vals], "source": src, "years": [y0, y1]}
    blob.upload_from_string(json.dumps(out), content_type="application/json")
    return out


def _span(months: list[int]) -> str:
    """Month numbers (1-12) as 'Jun–Sep' when consecutive (wrapping the year), else a list."""
    if len(months) == 12:
        return "all year"
    run = all((months[i] % 12) + 1 == months[i + 1] for i in range(len(months) - 1))
    return f"{MONTHS[months[0] - 1]}–{MONTHS[months[-1] - 1]}" if run else ", ".join(MONTHS[m - 1] for m in sorted(months))


def seasons(clim: dict | None) -> dict:
    """Season windows for a lake from its rainfall climatology. Offsets count months from the water-year start."""
    mm = (clim or {}).get("mm") or DEFAULT_CLIMATOLOGY
    total = sum(mm) or 1.0
    share = [sum(mm[(i + k) % 12] for k in range(4)) / total for i in range(12)]
    s0 = max(range(12), key=lambda i: share[i])          # 0-based first month of the wettest 4
    weak = share[s0] < WEAK_SHARE
    if weak:  # no clear rainy season: calendar years, judged on the whole year and its 4 driest months
        start, rain, peak = 0, list(range(12)), list(range(12))
        dry = sorted(sorted(range(12), key=lambda m: mm[m])[:4])
    else:
        start = (s0 + 1) % 12                              # water year starts the month after the rains begin
        rain, peak, dry = [-1, 0, 1, 2], [1, 2, 3, 4], [8, 9, 10, 11]
    month = lambda off: (start + off) % 12 + 1  # noqa: E731
    out = {"start_month": start + 1, "rain_offsets": rain, "peak_offsets": peak, "dry_offsets": dry, "weak": weak,
           "rain_months": [month(o) for o in rain], "peak_months": [month(o) for o in peak], "dry_months": [month(o) for o in dry],
           "climatology_mm": mm, "source": (clim or {}).get("source"), "wettest_4_share": round(share[s0], 2)}
    out |= {"rain": _span(out["rain_months"]), "peak": _span(out["peak_months"]), "dry": _span(out["dry_months"]),
            "water_year": f"{MONTHS[start]}–{MONTHS[(start + 11) % 12]}"}
    return out


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
    lat = _first_lat(geom)
    rain_col, rain_band, rain_scale, rain_factor, _ = _rain_source(lat)
    rain_area = g.centroid(10).buffer(max(5000, rain_scale * 1.5))
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
        ch = rain_col.filterDate(d, e).select(rain_band)
        rain = ee.Algorithms.If(ch.size().gt(0), ee.Number(ch.sum().reduceRegion(ee.Reducer.mean(), rain_area, rain_scale)
                                                          .get(rain_band)).multiply(rain_factor), None)
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


def _first_lat(geom: dict) -> float:
    c = geom["coordinates"]
    while isinstance(c[0], list):
        c = c[0]
    return float(c[1])


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


def hydro_years(rows: list[dict], outline_ha: float, season: dict | None = None) -> list[dict]:
    """One row per water year (the lake's own season; Jul-Jun for Delhi)."""
    season = season or seasons(None)
    vals = {int(r["month"][:4]) * 12 + int(r["month"][5:7]) - 1: r for r in rows}  # absolute month index
    if not vals:
        return []
    wet_thr = max(0.2, 0.1 * outline_ha)
    st = season["start_month"] - 1
    a = (min(vals) // 12 - 1) * 12 + st
    out = []
    while a <= max(vals):
        def get(offsets, k, a=a):
            return [(vals.get(a + o) or {}).get(k) for o in offsets]
        peak_vals = get(season["peak_offsets"], "water_ha")
        dry_vals = get(season["dry_offsets"], "water_ha")
        year_vals = get(range(12), "water_ha")
        rain_vals = get(season["rain_offsets"], "rain_mm")
        green_dry = get(season["dry_offsets"], "green_ha")
        n_peak, n_dry = sum(v is not None for v in peak_vals), sum(v is not None for v in dry_vals)
        ys, ms = divmod(a, 12)
        if n_peak >= 2:
            out.append({
                "year": ys, "label": str(ys) if ms == 0 else f"{ys}-{str(ys + 1)[2:]}", "start_idx": a, "end_idx": a + 11,
                "peak_ha": round(max(v for v in peak_vals if v is not None), 2),
                "dry_ha": round(_mean(dry_vals), 2) if n_dry >= 2 else None,
                "wet_months": sum(1 for v in year_vals if v is not None and v >= wet_thr),
                "months_observed": sum(v is not None for v in year_vals),
                "season_rain_mm": round(sum(rain_vals), 0) if all(v is not None for v in rain_vals) else None,
                "green_dry_share": round(_mean(green_dry) / outline_ha, 2) if _mean(green_dry) is not None and outline_ha else None,
                "complete": n_dry >= 2,
            })
        a += 12
    return out


def _material(gain: float | None, base: float | None, outline_ha: float) -> bool:
    if gain is None:
        return False
    return gain >= max(0.2, 0.1 * outline_ha) and gain >= 0.2 * (base or 0)


def evaluate(rows: list[dict], outline_ha: float, intervention: str, depth_m: float = DEFAULT_DEPTH_M,
             claimed_m3: float | None = None, climate: dict | None = None) -> dict:
    """Compare water years before and after `intervention` (YYYY-MM), on the lake's own seasons."""
    season = seasons(climate)
    i_idx = int(intervention[:4]) * 12 + int(intervention[5:7]) - 1
    hy = hydro_years(rows, outline_ha, season)
    # baseline: water years that ended by the intervention; post: water years that began after it
    base = [h for h in hy if h["end_idx"] <= i_idx][-3:]
    post = [h for h in hy if h["start_idx"] >= i_idx]
    season_public = {k: season[k] for k in ("water_year", "rain", "peak", "dry", "weak", "rain_months", "peak_months",
                                            "dry_months", "climatology_mm", "source")}
    res = {"intervention": intervention, "outline_ha": outline_ha, "years": hy, "season": season_public,
           "baseline_years": [h["label"] for h in base], "post_years": [h["label"] for h in post],
           "depth_m": depth_m, "claimed_m3_yr": claimed_m3}
    if not base:
        return res | {"verdict": "insufficient_data", "verdict_text": VERDICTS["insufficient_data"], "credited_m3_yr": 0}
    if not post:
        return res | {"verdict": "too_early", "verdict_text": VERDICTS["too_early"], "credited_m3_yr": 0}

    b_peak = _mean([h["peak_ha"] for h in base])
    b_max = max(h["peak_ha"] for h in base)
    b_dry = _mean([h["dry_ha"] for h in base])
    b_rain = _mean([h["season_rain_mm"] for h in base])
    b_wet = _mean([h["wet_months"] for h in base])
    for h in post:
        ratio = (h["season_rain_mm"] / b_rain) if (h["season_rain_mm"] and b_rain) else 1.0
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
                     "season_rain_mm": round(b_rain) if b_rain else None, "wet_months": round(b_wet, 1)},
        "judged_year": judged["label"],
        "recent": {"months": [rows[-6]["month"], rows[-1]["month"]] if len(rows) >= 6 else None,
                   "open_water_ha": round(recent_water, 2) if recent_water is not None else None,
                   "green_cover_ha": round(recent_green, 2) if recent_green is not None else None},
        "credited_m3_yr": round(credited, -2),
        "radar_optical_agreement": round(sum(agree) / len(agree), 2) if agree else None,
        "assumptions": [
            f"Seasons from local rainfall ({season['source'] or 'default monsoon'}, 1991-2020): rains {season['rain']}, "
            f"lake judged fullest {season['peak']}, dry season {season['dry']}, water year {season['water_year']}"
            + (" (no clear rainy season, so calendar years)." if season["weak"] else "."),
            f"Credited volume = extra peak water area beyond the rain-scaled baseline x {depth_m} m depth, "
            "assuming one fill per rainy season (indicative; depth isn't visible from space).",
            f"Expected peak = baseline peak x ({season['rain']} rain / baseline rain)^0.5, never above the largest "
            "pre-project peak in a wetter year (lakes saturate; rain ratio capped 0.5-2x).",
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
    clim = {"mm": list(p["climatology_mm"]), "source": p.get("climate_source")} if p.get("climatology_mm") else None
    ev = evaluate(reg["series"].get(project_id, []), p["outline_ha"], p["intervention_date"], p["depth_m"], p["claimed_m3_yr"], clim)
    return _brief(p) | {"geometry": p["geometry"], "evaluation": ev, "series": reg["series"].get(project_id, []),
                        "sources": SOURCES + ([clim["source"]] if clim and clim["source"] else []) + [PROJECTS_TABLE, SERIES_TABLE]}


def evidence(ev: dict) -> str | None:
    """One line of the numbers behind a verdict, for the agent to quote."""
    h = next((y for y in ev.get("years", []) if y["label"] == ev.get("judged_year")), None)
    if not h or "baseline" not in ev:
        return None
    b, r = ev["baseline"], ev.get("recent") or {}
    parts = [f"{h['label']}: peak ({ev['season']['peak']}) {h['peak_ha']} ha open water vs {h.get('expected_peak_ha')} ha expected from rain "
             f"(baseline {b['peak_ha']} ha)"]
    if h.get("dry_ha") is not None and b.get("dry_ha") is not None:
        parts.append(f"dry season ({ev['season']['dry']}) {h['dry_ha']} ha vs {b['dry_ha']} ha baseline")
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
    clim = climatology(lon, lat)
    return {"project_id": None, "name": name or "Water body", "lon": lon, "lat": lat, "outline_ha": oha,
            "intervention_date": intervention, "depth_m": depth_m, "claimed_m3_yr": claimed_m3, "is_demo": False,
            "geometry": geom, "evaluation": evaluate(rows, oha, intervention, depth_m, claimed_m3, clim), "series": rows,
            "sources": SOURCES + [clim["source"]] + ([ERA5_MONTHLY] if abs(lat) >= CHIRPS_MAX_LAT else [])}


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
        bigquery.SchemaField("climatology_mm", "FLOAT64", "REPEATED"), bigquery.SchemaField("climate_source", "STRING"),
        bigquery.SchemaField("is_demo", "BOOL"),
    ],
    SERIES_TABLE: [
        bigquery.SchemaField("project_id", "STRING", "REQUIRED"), bigquery.SchemaField("month", "DATE"),
        bigquery.SchemaField("water_ha", "FLOAT64"), bigquery.SchemaField("optical_water_ha", "FLOAT64"),
        bigquery.SchemaField("green_ha", "FLOAT64"), bigquery.SchemaField("rain_mm", "FLOAT64"),
        bigquery.SchemaField("s1_images", "INT64"),
    ],
}
