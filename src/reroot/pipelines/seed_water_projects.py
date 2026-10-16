"""Seed the demo replenishment portfolio and its monthly satellite water history.

    uv run python -m reroot.pipelines.seed_water_projects

The lakes and every satellite number are real. The sponsor (a 50 MW AI data centre at Gwal
Pahari), its claimed volumes and, except for Badkhal, the intervention dates are illustrative
(is_demo). Badkhal Lake's revival is a real public project: launched 2018, with treated water
from a 10 MLD sewage treatment plant reaching the lake from 2023 (news reports in `references`).
The portfolio's target is 120% of the data centre's freshwater use from assess_datacenter_site.
"""

import json

from google.cloud import bigquery

from reroot.api import datacenter, explore, water
from reroot.core.clients import bq, init_ee

SPONSOR = "Demo: 50 MW AI data centre, Gwal Pahari"
# (id, name, place, lon, lat, search_m, intervention, date, depth_m, claim share of target, notes, references)
PROJECTS = [
    ("W1", "Badkhal Lake revival", "Faridabad", 77.2754, 28.4168, 2000,
     "Lake-bed works and 10 MLD of treated water from the Sector 21 sewage treatment plant", "2023-06", 1.0, 0.45,
     "Real public project: launched 2018, dry since about 2001, treated-water inflow from 2023. Sponsor link and claimed volume are illustrative.",
     ["https://www.tribuneindia.com/news/haryana/launched-in-2018-badkhal-lake-revival-project-behind-schedule-491076",
      "https://www.downtoearth.org.in/water/treated-sewage-water-in-faridabads-badkhal-lake-blamed-for-tree-deaths-and-groundwater-fears"]),
    ("W2", "Najafgarh Jheel wetland restoration", "Najafgarh, Delhi–Gurugram border", 76.9589, 28.5046, 3000,
     "Wetland restoration and inflow management (sponsor funds a share)", "2021-06", 0.5, 0.35,
     "Illustrative project record on a real wetland.", []),
    ("W3", "Damdama Lake desilting", "Damdama, Gurugram", 77.1317, 28.3073, 2000,
     "Desilting 0.5 m and clearing feeder channels", "2022-03", 0.5, 0.30,
     "Illustrative project record on a real seasonal lake.", []),
    ("W4", "Neeli Jheel restoration", "Faridabad", 77.2493, 28.4483, 2000,
     "Shoreline restoration and desilting", "2021-03", 0.5, 0.15,
     "Illustrative project record on a real lake (a flooded former mine pit).", []),
]
FALLBACK_TARGET_M3 = 340_000


def sponsor_target() -> float:
    try:
        hit = explore.find_place("Gwal Pahari")[0]
        d = datacenter.assess_site(hit["lon"], hit["lat"], "Gwal Pahari", 50, "evaporative")
        return float(d["chosen"]["replenishment_target_m3"])
    except Exception as e:  # noqa: BLE001 - Overpass is flaky; the target is only context
        print("target fallback:", e)
        return FALLBACK_TARGET_M3


def main() -> None:
    init_ee()
    target = sponsor_target()
    print(f"sponsor target {target:,.0f} m3/yr")
    projects, series_rows = [], []
    for pid, name, place, lon, lat, search_m, what, when, depth, share, notes, refs in PROJECTS:
        geom = water.outline(lon, lat, search_m=search_m)
        oha = water.area_ha(geom)
        rows = water.series(geom)
        ev = water.evaluate(rows, oha, when, depth, round(target * share, -3))
        print(f"{pid} {name}: {oha} ha, {len(rows)} months -> {ev['verdict']}")
        projects.append({"project_id": pid, "name": name, "place": place, "lon": lon, "lat": lat,
                         "geojson": json.dumps(geom), "outline_ha": oha, "sponsor": SPONSOR,
                         "sponsor_target_m3_yr": round(target, -3), "intervention": what, "intervention_date": when,
                         "depth_m": depth, "claimed_m3_yr": round(target * share, -3), "notes": notes,
                         "references": refs, "is_demo": True})
        series_rows += [r | {"project_id": pid, "month": r["month"] + "-01"} for r in rows]
    for table, rows in ((water.PROJECTS_TABLE, projects), (water.SERIES_TABLE, series_rows)):
        cfg = bigquery.LoadJobConfig(schema=water.SCHEMA[table], write_disposition="WRITE_TRUNCATE")
        bq().load_table_from_json(rows, table, job_config=cfg).result()
        print(f"loaded {len(rows)} rows into {table}")


if __name__ == "__main__":
    main()
