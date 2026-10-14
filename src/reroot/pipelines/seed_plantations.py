"""Seed demo plantations and their monthly satellite history.

    uv run python -m reroot.pipelines.seed_plantations

The plantation *records* are illustrative (names, planting dates, caretakers are demo data,
flagged with is_demo). Their geometry and NDVI history are real: two are clusters of cells
that actually turned into tree cover between 2018 and 2025, one is degraded scrub that never
recovered. Expected trajectory = a nearby mature forest's NDVI, ramped up over the first
five years after planting.
"""

import json
from datetime import date
from pathlib import Path

import ee
from google.cloud import bigquery

from reroot.api.species import shortlist
from reroot.core import config, layers as L
from reroot.core.clients import bq, init_ee

DS = f"{config.PROJECT}.{config.BQ_DATASET}"
DEMO = json.loads((Path(__file__).resolve().parents[1] / "data/demo_project.json").read_text())

DEMO_PLANTATIONS = [
    # (id, name, planted, kind, caretaker, source_project)
    ("P1", "Bandhwari ridge restoration", date(2019, 7, 15), "gained", "Bandhwari women's SHG (demo)", "NH bypass CA batch 2019 (demo)"),
    ("P2", "Mangar foothills block", date(2020, 8, 1), "gained", "Mangar village caretakers (demo)", "CSR programme 2020 (demo)"),
    ("P3", "Pali scrubland block", date(2021, 7, 20), "stalled", "Pali gram panchayat (demo)", "CA batch 2021 (demo)"),
]

CLUSTER_SQL = """
WITH p AS (SELECT ST_GEOGFROMGEOJSON(@demo, make_valid => TRUE) g),
cand AS (
  SELECT c.* FROM `{ds}.grid_cells` c, p
  WHERE c.region = @region AND ST_DWITHIN(c.centroid, p.g, 12000) AND NOT ST_INTERSECTS(c.geom, p.g)
    AND {where}
),
cl AS (SELECT *, ST_CLUSTERDBSCAN(centroid, 110, 3) OVER () AS cid FROM cand)
SELECT cid, COUNT(*) n, ST_ASGEOJSON(ST_SIMPLIFY(ST_UNION_AGG(geom), 5)) gj,
       AVG(slope_deg) slope, AVG(frac_bare) bare, AVG(dist_water_m) dw, AVG(soil_moisture) sm
FROM cl WHERE cid IS NOT NULL GROUP BY cid HAVING n BETWEEN 8 AND 40 ORDER BY n DESC LIMIT 4
"""
WHERE = {
    "gained": "c.label_tree_gain = 1",
    "stalled": "c.label_tree_gain = 0 AND c.frac_shrub + c.frac_bare > 0.6",
}


def pick_geometries() -> dict[str, list]:
    picked = {}
    for kind in ("gained", "stalled"):
        q = CLUSTER_SQL.format(ds=DS, where=WHERE[kind])
        picked[kind] = list(bq().query(q, job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("demo", "STRING", json.dumps(DEMO)),
            bigquery.ScalarQueryParameter("region", "STRING", config.DEFAULT_REGION)])).result())
    return picked


def seed_records(picked: dict) -> list[dict]:
    records, queues = [], {k: list(v) for k, v in picked.items()}
    for pid, name, planted, kind, caretaker, source in DEMO_PLANTATIONS:
        r = queues[kind].pop(0)
        area_ha = float(r.n)
        records.append({
            "plantation_id": pid, "name": name, "geom": r.gj, "planted_date": planted.isoformat(),
            "species": [s["local"] + " (" + s["name"] + ")" for s in shortlist(r.slope, r.bare, r.dw, r.sm)],
            "saplings_planted": int(area_ha * 1100), "caretaker_name": caretaker, "caretaker_chat_id": None,
            "language": "hi-IN", "source_project": source, "is_demo": True,
        })
    # DML, not streaming inserts: streamed rows can't be deleted for ~30 min, which breaks re-seeding.
    bq().query(f"DELETE FROM `{DS}.plantations` WHERE TRUE").result()
    bq().query(f"""
INSERT INTO `{DS}.plantations` (plantation_id, name, geom, planted_date, species, saplings_planted,
  caretaker_name, caretaker_chat_id, language, source_project, is_demo)
SELECT plantation_id, name, ST_GEOGFROMGEOJSON(geom, make_valid => TRUE), DATE(planted_date), species,
  saplings_planted, caretaker_name, caretaker_chat_id, language, source_project, is_demo
FROM UNNEST(@rows)""", job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ArrayQueryParameter(
        "rows", "STRUCT", [bigquery.StructQueryParameter(
            None, *[_param(k, v) for k, v in r.items()]) for r in records])])).result()
    for r in records:
        print(f"  {r['plantation_id']} {r['name']}: {r['saplings_planted'] // 1100} ha, planted {r['planted_date']}")
    return records


def monthly_ndvi(records: list[dict]) -> None:
    feats = [ee.Feature(ee.Geometry(json.loads(r["geom"])), {"pid": r["plantation_id"]}) for r in records]
    feats.append(ee.Feature(ee.Geometry.Point(config.DEMO_REFERENCE_POINT).buffer(500), {"pid": "REF"}))
    fc = ee.FeatureCollection(feats)
    region = fc.geometry().bounds()
    rows = []
    for year in range(2019, 2027):
        months = [m for m in range(1, 13) if (year, m) <= (2026, 9)]

        def month_fc(m, year=year):
            start = ee.Date.fromYMD(year, m, 1)
            nd = L.ndvi(L.s2_composite(region, start, start.advance(1, "month")))
            return nd.reduceRegions(fc, ee.Reducer.mean(), 10).map(
                lambda f: f.set("month", start.format("YYYY-MM-01")))

        out = ee.FeatureCollection([month_fc(m) for m in months]).flatten().getInfo()["features"]
        rows += [f["properties"] for f in out]
        print(f"  NDVI {year}: {len(out)} values", flush=True)

    ref = {r["month"]: r.get("mean") for r in rows if r["pid"] == "REF"}
    planted = {r["plantation_id"]: date.fromisoformat(r["planted_date"]) for r in records}
    out_rows = []
    for r in rows:
        if r["pid"] == "REF" or r.get("mean") is None:
            continue
        d = date.fromisoformat(r["month"])
        years = max(0.0, (d - planted[r["pid"]]).days / 365.25)
        ramp = min(1.0, 0.5 + 0.1 * years)  # target reaches mature-forest NDVI five years after planting
        expected = ref.get(r["month"]) * ramp if ref.get(r["month"]) is not None else None
        out_rows.append({"plantation_id": r["pid"], "date": r["month"], "ndvi_mean": round(r["mean"], 4),
                         "ndvi_expected": round(expected, 4) if expected is not None else None})
    table = bq().get_table(f"{DS}.plantation_timeseries")
    bq().load_table_from_json(out_rows, table, job_config=bigquery.LoadJobConfig(
        schema=table.schema, write_disposition="WRITE_TRUNCATE")).result()
    print(f"  wrote {len(out_rows)} monthly rows")


def main():
    init_ee()
    _ensure_columns()
    records = seed_records(pick_geometries())
    monthly_ndvi(records)


def _param(name, value):
    if isinstance(value, list):
        return bigquery.ArrayQueryParameter(name, "STRING", value)
    if isinstance(value, bool):
        return bigquery.ScalarQueryParameter(name, "BOOL", value)
    if isinstance(value, int):
        return bigquery.ScalarQueryParameter(name, "INT64", value)
    return bigquery.ScalarQueryParameter(name, "STRING", value)


def _ensure_columns():
    bq().query(f"ALTER TABLE `{DS}.plantations` ADD COLUMN IF NOT EXISTS is_demo BOOL").result()


if __name__ == "__main__":
    main()
