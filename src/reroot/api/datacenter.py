"""Water-positive data centre screening and industrial-symbiosis matching.

For a candidate site: how water-stressed it is (WRI Aqueduct 4.0, GRACE groundwater trend,
rainfall), how much water its cooling would use (physics from ERA5-Land hourly climate), and
which neighbours could close the loops (OpenStreetMap): treated wastewater in, clean power in,
waste heat out, and replenishment projects (ponds to rejuvenate, land to restore).

Screening-grade: every number carries its assumption so an engineer can replace it.
"""

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor

import ee
import requests
from google.cloud import bigquery

from reroot.api.alerts import _bucket
from reroot.core import config, regions
from reroot.core.clients import bq

AQUEDUCT = "WRI/Aqueduct_Water_Risk/V4/baseline_annual"
GRACE = "NASA/GRACE/MASS_GRIDS_V04/MASCON_CRI"
ERA5_DAILY = "ECMWF/ERA5_LAND/DAILY_AGGR"
CHIRPS = "UCSB-CHG/CHIRPS/DAILY"
SOURCES = [AQUEDUCT, GRACE, ERA5_DAILY, CHIRPS, "OpenStreetMap (Overpass)"]

OVERPASS = ["https://maps.mail.ru/osm/tools/overpass/api/interpreter", "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
UA = "ReRoot/0.3 (hackathon demo; github.com/saketharshraj/reroot)"

# Cooling physics (assumptions are returned with the result).
LATENT_HEAT_MJ_PER_KG = 2.45            # water evaporated per unit heat rejected
L_EVAP_PER_KWH = 3.6 / LATENT_HEAT_MJ_PER_KG   # ~1.47 L per kWh of heat
CYCLES_OF_CONCENTRATION = 4              # cooling-tower blowdown = evaporation / (COC - 1)
L_PER_KWH_TOWER = L_EVAP_PER_KWH * (1 + 1 / (CYCLES_OF_CONCENTRATION - 1))  # ~1.96 L/kWh
HEAT_PER_IT_KWH = 1.1                    # heat rejected per kWh of IT load (IT + share of losses)
DRY_COOLING_MAX_C = 25                   # hybrid systems run dry below this air temperature
RECLAIMED_SHARE = 0.7                    # share of make-up water a nearby STP could supply
REPLENISH_TARGET = 1.2                   # 120% replenishment, Google's 2030 ambition
POND_DESILT_M = 1.5                      # extra storage per pond from desilting, refilled once a year
HEAT_RADIUS_KM = 5                       # low-grade heat (30–45 °C) doesn't travel far
RECLAIMED_MAX_KM = 15                    # treated-wastewater pipeline considered feasible

CATEGORY = {
    "reclaimed_water": ('nwr["man_made"="wastewater_plant"]', "Treated wastewater for cooling instead of freshwater"),
    "clean_power": ('nwr["power"="plant"]["plant:source"~"solar|wind"]', "Clean power: PPA or co-located supply"),
    "heat_user": ('nwr["landuse"="greenhouse_horticulture"];nwr["industrial"="cold_storage"];nwr["building"="greenhouse"];nwr["landuse"="industrial"]["name"]',
                  "Low-grade waste heat for absorption chilling, drying or greenhouses"),
    "water_body": ('nwr["natural"="water"]["name"]', "Replenishment: desilt and recharge"),
    "data_centre": ('nwr["telecom"="data_center"];nwr["building"="data_center"]', "Existing data centre (shared infrastructure)"),
}


INDUSTRIAL_ESTATE = re.compile(r"industrial|estate|udyog|\bimt\b|industry|industries|manufactur|factory|\bpark\b|\barea\b", re.I)
NOT_HEAT = re.compile(r"depot|yard|parking|metro|bus|railway|waste|dump|landfill", re.I)


def _km(lon1, lat1, lon2, lat2) -> float:
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def _category(tags: dict) -> str | None:
    if tags.get("man_made") == "wastewater_plant":
        return "reclaimed_water"
    if tags.get("power") == "plant":
        return "clean_power"
    if tags.get("telecom") == "data_center" or tags.get("building") == "data_center":
        return "data_centre"
    if tags.get("natural") == "water":
        return "water_body"
    if tags.get("landuse") == "greenhouse_horticulture" or tags.get("industrial") == "cold_storage" \
            or tags.get("building") == "greenhouse":
        return "heat_user"
    # Industrial land includes depots and yards; keep estates that can take process heat.
    if tags.get("landuse") == "industrial" and INDUSTRIAL_ESTATE.search(tags.get("name", "")) \
            and not NOT_HEAT.search(tags.get("name", "")):
        return "heat_user"
    return None


def _overpass(area_filter: str) -> list[dict] | None:
    """Run the partner query with an area filter like '(around:15000,lat,lon)' or '(s,w,n,e)'.
    Public Overpass servers are often busy, so try several mirrors; None means all failed."""
    parts = [f"{sel}{area_filter};" for selectors, _ in CATEGORY.values() for sel in selectors.split(";")]
    query = f"[out:json][timeout:90];({''.join(parts)});out bb tags 2000;"
    for attempt in range(2):
        for url in OVERPASS:
            try:
                resp = requests.post(url, data={"data": query}, headers={"User-Agent": UA}, timeout=100)
                body = resp.json() if resp.ok and "json" in resp.headers.get("content-type", "") else None
            except (requests.RequestException, ValueError):
                continue
            # Busy servers answer 502/500, or 200 with a "remark" and no elements: try the next one.
            if body and "elements" in body and not (body.get("remark") and not body["elements"]):
                return _parse(body["elements"])
        time.sleep(3)
    return None


def _parse(elements: list[dict]) -> list[dict]:
    out = []
    for el in elements:
        tags = el.get("tags", {})
        cat = _category(tags)
        b = el.get("bounds")
        c = ({"lat": (b["minlat"] + b["maxlat"]) / 2, "lon": (b["minlon"] + b["maxlon"]) / 2} if b
             else {"lat": el.get("lat"), "lon": el.get("lon")})
        if not cat or c.get("lat") is None:
            continue
        area_m2 = None
        if b:
            w = _km(b["minlon"], b["minlat"], b["maxlon"], b["minlat"]) * 1000
            h = _km(b["minlon"], b["minlat"], b["minlon"], b["maxlat"]) * 1000
            area_m2 = round(w * h * 0.6)  # bounding box -> rough footprint
        out.append({"name": tags.get("name") or tags.get("operator") or _default_name(cat, tags),
                    "category": cat, "lon": c["lon"], "lat": c["lat"], "area_m2": area_m2,
                    "osm": f"https://www.openstreetmap.org/{el['type']}/{el['id']}"})
    return out


def _default_name(cat: str, tags: dict) -> str:
    if cat == "heat_user":
        return {"greenhouse_horticulture": "Greenhouses", "industrial": "Industrial area"}.get(
            tags.get("landuse"), "Cold storage" if tags.get("industrial") == "cold_storage" else "Greenhouse")
    return {"reclaimed_water": "Sewage treatment plant", "clean_power": "Solar plant" if "solar" in tags.get("plant:source", "") else "Wind farm",
            "water_body": "Pond", "data_centre": "Data centre"}.get(cat, cat)


_region_cache: dict[str, list[dict]] = {}


def region_features(region_id: str, margin_km: float = 15) -> list[dict]:
    """All partner candidates for a region plus a margin, fetched once and kept in GCS."""
    if region_id in _region_cache:
        return _region_cache[region_id]
    blob = _bucket().blob(f"symbiosis/region/{region_id}.json")
    try:
        _region_cache[region_id] = json.loads(blob.download_as_bytes())
        return _region_cache[region_id]
    except Exception:  # noqa: BLE001 - not cached yet
        pass
    w, s, e, n = regions.spec(region_id)["bbox"]
    dlat = margin_km / 111.32
    dlon = margin_km / (111.32 * math.cos(math.radians((s + n) / 2)))
    feats = _overpass(f"({s - dlat},{w - dlon},{n + dlat},{e + dlon})")
    if feats:
        blob.upload_from_string(json.dumps(feats), content_type="application/json")
        _region_cache[region_id] = feats
    return feats or []


def neighbours(lon: float, lat: float, radius_km: float) -> list[dict]:
    """Partner candidates around a site: from the region cache when the site is in a loaded
    region, otherwise a live (best-effort) query."""
    rid = regions.containing(lon, lat, ready_only=False)
    feats = region_features(rid) if rid else (_overpass(f"(around:{int(radius_km * 1000)},{lat},{lon})") or [])
    out = []
    for f in feats:
        d = _km(lon, lat, f["lon"], f["lat"])
        limit = HEAT_RADIUS_KM * 2 if f["category"] == "heat_user" else radius_km
        if d <= limit:
            out.append(f | {"distance_km": round(d, 1)})
    out.sort(key=lambda x: x["distance_km"])
    return out


def climate_and_water(lon: float, lat: float) -> dict:
    """One Earth Engine round trip: Aqueduct indicators, groundwater trend, cooling hours, rain, sun."""
    pt = ee.Geometry.Point([lon, lat])
    aq = ee.FeatureCollection(AQUEDUCT).filterBounds(pt).first()
    aq_props = ee.Dictionary(ee.Algorithms.If(aq, ee.Feature(aq).toDictionary(
        ["bws_label", "bws_raw", "bwd_label", "gtd_label", "drr_label", "iav_label", "name_1"]), {}))

    # Hours above a threshold from daily min/max with a sinusoidal day (365 images, not 8,760):
    # hours = 24/pi * acos((2T - Tmax - Tmin) / (Tmax - Tmin)), clamped to 0..24.
    days = ee.ImageCollection(ERA5_DAILY).filterDate("2025-01-01", "2026-01-01").select(["temperature_2m_max", "temperature_2m_min"])

    def hours_above(threshold_c):
        t = 273.15 + threshold_c

        def per_day(img):
            tmax, tmin = img.select(0), img.select(1)
            x = ee.Image(2 * t).subtract(tmax).subtract(tmin).divide(tmax.subtract(tmin).max(0.1)).clamp(-1, 1)
            return x.acos().multiply(24 / math.pi)
        return days.map(per_day).sum()

    hot = hours_above(DRY_COOLING_MAX_C).rename("hours_above")
    very_hot = hours_above(35).rename("hours_above_35")
    hours = days.select(0).count().multiply(24).rename("hours")

    grace = ee.ImageCollection(GRACE).filterDate("2015-01-01", "2025-01-01").select("lwe_thickness")
    with_t = grace.map(lambda i: ee.Image.constant(ee.Date(i.get("system:time_start")).difference(ee.Date("2015-01-01"), "year"))
                       .float().rename("t").addBands(i))
    trend = with_t.select(["t", "lwe_thickness"]).reduce(ee.Reducer.linearFit()).select("scale").rename("gw_cm_per_yr")

    rain = ee.ImageCollection([ee.ImageCollection(CHIRPS).filterDate(f"{y}-01-01", f"{y + 1}-01-01").sum() for y in range(2020, 2026)]) \
        .mean().rename("rain_mm")
    sun = ee.ImageCollection(ERA5_DAILY).filterDate("2025-01-01", "2026-01-01").select("surface_solar_radiation_downwards_sum") \
        .sum().divide(3.6e6).rename("solar_kwh_m2")

    stats = ee.Image.cat([hot, very_hot, hours, trend, rain, sun]).reduceRegion(ee.Reducer.first(), pt, 11000)
    return ee.Dictionary({"aq": aq_props, "s": stats}).getInfo()


def _plantable_ha(lon: float, lat: float, radius_km: float) -> int | None:
    rid = regions.containing(lon, lat)
    if not rid:
        return None
    from reroot.api.explore import ELIGIBLE
    row = list(bq().query(
        f"SELECT COUNTIF({ELIGIBLE}) n FROM `{config.PROJECT}.{config.BQ_DATASET}.grid_cells` "
        "WHERE region = @r AND ST_DWITHIN(centroid, ST_GEOGPOINT(@lon, @lat), @m)",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("r", "STRING", rid), bigquery.ScalarQueryParameter("lon", "FLOAT64", lon),
            bigquery.ScalarQueryParameter("lat", "FLOAT64", lat), bigquery.ScalarQueryParameter("m", "FLOAT64", radius_km * 1000)])).result())
    return int(row[0].n or 0)


def assess_site(lon: float, lat: float, name: str | None = None, it_mw: float = 50, cooling: str = "evaporative",
                radius_km: float = 15) -> dict:
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_env = pool.submit(climate_and_water, lon, lat)
        f_osm = pool.submit(neighbours, lon, lat, radius_km)
        f_land = pool.submit(_plantable_ha, lon, lat, min(radius_km, 10))
        env, partners, plantable = f_env.result(), f_osm.result(), f_land.result()
    aq, st = env.get("aq", {}), env.get("s", {})

    hours = st.get("hours") or 8760
    share_hot = (st.get("hours_above") or 0) / hours
    heat_kwh = it_mw * 1000 * 8760 * HEAT_PER_IT_KWH
    scenarios = {
        "evaporative": heat_kwh * L_PER_KWH_TOWER / 1000,                 # towers all year
        "hybrid": heat_kwh * share_hot * L_PER_KWH_TOWER / 1000,          # dry when air < 25 °C
        "closed_loop": heat_kwh * L_PER_KWH_TOWER * 0.03 / 1000,          # air/liquid closed loop: make-up only
    }
    cooling = cooling if cooling in scenarios else "evaporative"
    water_m3 = scenarios[cooling]

    stp = [p for p in partners if p["category"] == "reclaimed_water"]
    nearest_stp = stp[0] if stp else None
    reclaimed_m3 = water_m3 * RECLAIMED_SHARE if nearest_stp and nearest_stp["distance_km"] <= RECLAIMED_MAX_KM else 0.0
    fresh_m3 = water_m3 - reclaimed_m3
    replenish_m3 = fresh_m3 * REPLENISH_TARGET

    ponds = sorted((p for p in partners if p["category"] == "water_body" and p.get("area_m2")), key=lambda p: -p["area_m2"])
    covered, chosen_ponds = 0.0, []
    for p in ponds:
        if covered >= replenish_m3:
            break
        v = p["area_m2"] * POND_DESILT_M
        covered += v
        chosen_ponds.append({"name": p["name"], "distance_km": p["distance_km"], "area_ha": round(p["area_m2"] / 1e4, 1),
                             "added_storage_m3": round(v)})

    heat_users = [p for p in partners if p["category"] == "heat_user" and p["distance_km"] <= HEAT_RADIUS_KM]
    power = [p for p in partners if p["category"] == "clean_power"]

    def unique(items):
        seen, out = set(), []
        for p in items:
            if p["name"] not in seen:
                seen.add(p["name"])
                out.append(p)
        return out

    power, heat_users = unique(power), unique(heat_users)
    flows = []
    if nearest_stp:
        flows.append({"from": nearest_stp, "to": "site", "kind": "reclaimed_water", "label": f"Treated wastewater ({round(reclaimed_m3 / 1e3):,} ML/yr)" if reclaimed_m3 else "Treated wastewater (too far for a pipeline)"})
    for p in power[:2]:
        flows.append({"from": p, "to": "site", "kind": "clean_power", "label": "Clean power"})
    for p in heat_users[:3]:
        flows.append({"from": "site", "to": p, "kind": "heat", "label": "Waste heat"})
    by_name = {p["name"]: p for p in ponds}
    for cp in chosen_ponds[:5]:
        flows.append({"from": "site", "to": by_name[cp["name"]], "kind": "replenish", "label": "Replenishment funding"})

    return {
        "name": name, "site": [lon, lat], "it_load_mw": it_mw, "cooling": cooling, "radius_km": radius_km,
        "water_risk": {
            "baseline_water_stress": aq.get("bws_label"), "water_depletion": aq.get("bwd_label"),
            "groundwater_table_decline": aq.get("gtd_label"), "drought_risk": aq.get("drr_label"),
            "interannual_variability": aq.get("iav_label"), "state": aq.get("name_1"),
            "grace_groundwater_trend_cm_per_yr": round(st["gw_cm_per_yr"], 2) if st.get("gw_cm_per_yr") is not None else None,
            "rain_mm_per_yr": round(st["rain_mm"]) if st.get("rain_mm") is not None else None,
        },
        "climate": {"hours_above_25c": int(st.get("hours_above") or 0), "hours_above_35c": int(st.get("hours_above_35") or 0),
                    "share_hours_needing_evaporative": round(share_hot, 2),
                    "solar_kwh_per_m2_yr": round(st["solar_kwh_m2"]) if st.get("solar_kwh_m2") else None},
        "water_m3_per_yr": {k: round(v) for k, v in scenarios.items()},
        "water_ml_per_mw_yr": {k: round(v / 1e3 / it_mw, 1) for k, v in scenarios.items()},
        "chosen": {"total_m3": round(water_m3), "reclaimed_m3": round(reclaimed_m3), "freshwater_m3": round(fresh_m3),
                   "replenishment_target_m3": round(replenish_m3),
                   "ponds_to_rejuvenate": chosen_ponds,
                   "replenishment_covered_by_ponds": covered >= replenish_m3,
                   "ponds_nearby_count": len(ponds),
                   "plantable_ha_nearby": plantable},
        "partners": partners[:60],
        "partner_counts": {c: sum(1 for p in partners if p["category"] == c) for c in CATEGORY},
        "flows": flows,
        "assumptions": [
            f"Evaporative towers use ≈{L_PER_KWH_TOWER:.2f} L per kWh of heat ({L_EVAP_PER_KWH:.2f} L evaporates; blowdown at {CYCLES_OF_CONCENTRATION} cycles).",
            f"Heat rejected = {HEAT_PER_IT_KWH} × IT energy; IT load runs 24/7.",
            f"Hybrid cooling runs dry below {DRY_COOLING_MAX_C} °C (hours from ERA5-Land 2025 daily min/max with a sinusoidal day).",
            f"A treated-wastewater plant within {RECLAIMED_MAX_KM} km could supply {int(RECLAIMED_SHARE * 100)}% of make-up water (pipeline feasibility to be confirmed).",
            f"Replenishment target = {int(REPLENISH_TARGET * 100)}% of freshwater use; each pond adds {POND_DESILT_M} m of desilted storage, refilled yearly.",
            f"Low-grade heat (30–45 °C) is only matched to users within {HEAT_RADIUS_KM} km.",
        ],
        "sources": SOURCES,
        "seconds": round(time.time() - t0, 1),
    }
