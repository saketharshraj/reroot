"""Rank candidate planting sites around a project: score 1 ha cells in BigQuery, cluster
adjacent good cells into sites, then pick the best sites until the area target is met."""

import json
import uuid

from google.cloud import bigquery

from reroot.api.species import shortlist
from reroot.core import config
from reroot.core.clients import bq

WEIGHT_KEYS = ["establishment", "water", "connectivity", "people", "terrain"]
MIN_SITE_HA = 3

SQL = """
WITH project AS (SELECT ST_GEOGFROMGEOJSON(@geojson, make_valid => TRUE) AS g),
cells AS (
  SELECT c.*, p.ml_score, ST_INTERSECTS(c.geom, project.g) AS in_project
  FROM `{ds}.grid_cells` c
  JOIN `{ds}.cell_predictions` p USING (cell_id), project
  WHERE c.region = @region AND ST_DWITHIN(c.centroid, project.g, @radius_m)
),
flagged AS (
  SELECT *, CASE
    WHEN in_project THEN 'project_footprint'
    WHEN frac_trees > 0.5 THEN 'existing_forest'
    WHEN 'built' IN UNNEST(@exclude) AND frac_built > 0.2 THEN 'built'
    WHEN 'water' IN UNNEST(@exclude) AND frac_water > 0.2 THEN 'water'
    WHEN 'farmland' IN UNNEST(@exclude) AND frac_crops > 0.4 THEN 'farmland'
    WHEN 'grassland' IN UNNEST(@exclude) AND frac_grass > 0.4 THEN 'grassland'
    WHEN 'steep' IN UNNEST(@exclude) AND slope_deg > 20 THEN 'steep'
    WHEN 'protected' IN UNNEST(@exclude) AND is_protected THEN 'protected'
  END AS excluded_reason
  FROM cells
),
excluded AS (
  SELECT excluded_reason, COUNT(*) AS n FROM flagged WHERE excluded_reason IS NOT NULL GROUP BY 1
),
scored AS (
  SELECT *,
    PERCENT_RANK() OVER (ORDER BY ml_score) AS s_establishment,
    0.4 * LEAST(GREATEST((rain_annual_mm - 400) / 600, 0), 1)
      + 0.3 * (1 - LEAST(dist_water_m, 3840) / 3840)
      + 0.3 * LEAST(GREATEST((soil_moisture - 0.10) / 0.25, 0), 1) AS s_water,
    1 - LEAST(dist_forest_m, 3000) / 3000 AS s_connectivity,
    LEAST(LOG10(1 + pop_1km) / LOG10(1 + 50000), 1) AS s_people,
    1 - LEAST(slope_deg, 20) / 20 AS s_terrain
  FROM flagged WHERE excluded_reason IS NULL
),
final AS (
  SELECT *, (@w_establishment * s_establishment + @w_water * s_water + @w_connectivity * s_connectivity
             + @w_people * s_people + @w_terrain * s_terrain) / @w_total AS final_score
  FROM scored
),
top_cells AS (SELECT * FROM final ORDER BY final_score DESC LIMIT @max_cells),
clustered AS (SELECT *, ST_CLUSTERDBSCAN(centroid, 110, 2) OVER () AS cluster_id FROM top_cells)
SELECT
  cluster_id,
  SUM(area_ha) AS area_ha,
  AVG(final_score) AS final_score,
  AVG(s_establishment) AS establishment, AVG(s_water) AS water, AVG(s_connectivity) AS connectivity,
  AVG(s_people) AS people, AVG(s_terrain) AS terrain,
  AVG(slope_deg) AS slope_deg, AVG(rain_annual_mm) AS rain_annual_mm, AVG(soil_moisture) AS soil_moisture,
  AVG(dist_forest_m) AS dist_forest_m, AVG(dist_water_m) AS dist_water_m, AVG(pop_1km) AS pop_1km,
  AVG(frac_bare) AS frac_bare, AVG(frac_shrub) AS frac_shrub, AVG(frac_grass) AS frac_grass,
  AVG(ndvi_dry) AS ndvi_dry, AVG(ml_score) AS ml_score,
  ST_ASGEOJSON(ST_SIMPLIFY(ST_UNION_AGG(geom), 5)) AS geojson,
  ST_X(ST_CENTROID(ST_UNION_AGG(geom))) AS lon, ST_Y(ST_CENTROID(ST_UNION_AGG(geom))) AS lat,
  (SELECT COUNT(*) FROM flagged) AS cells_considered,
  (SELECT TO_JSON_STRING(ARRAY_AGG(STRUCT(excluded_reason, n))) FROM excluded) AS excluded_json
FROM clustered
WHERE cluster_id IS NOT NULL
GROUP BY cluster_id
HAVING SUM(area_ha) >= @min_site_ha
ORDER BY final_score DESC
"""


def _rationale(r) -> str:
    cover = "bare ground" if r.frac_bare > 0.4 else "open scrub" if r.frac_shrub > 0.3 else "degraded open land"
    parts = [f"Mostly {cover} on {'gentle' if r.slope_deg < 5 else 'moderate'} slopes ({r.slope_deg:.0f}°)"]
    if r.dist_forest_m < 300:
        parts.append("right next to an existing forest patch it would extend")
    elif r.dist_forest_m < 1500:
        parts.append(f"{r.dist_forest_m:,.0f} m from a forest patch it would help connect")
    parts.append(f"{r.rain_annual_mm:,.0f} mm rain a year")
    if r.establishment >= 0.6:
        parts.append("the model ranks it above most candidates for trees taking hold")
    if r.people >= 0.6:
        parts.append(f"about {r.pop_1km:,.0f} people live within 1 km")
    return "; ".join(parts) + "."


def search(req: dict) -> dict:
    weights = {k: float(req.get("weights", {}).get(k, 1)) for k in WEIGHT_KEYS}
    total = sum(weights.values()) or 1.0
    area_needed = float(req["area_ha_needed"])
    params = [
        bigquery.ScalarQueryParameter("geojson", "STRING", json.dumps(req["project_geometry"])),
        bigquery.ScalarQueryParameter("region", "STRING", req.get("region") or config.DEFAULT_REGION),
        bigquery.ScalarQueryParameter("radius_m", "FLOAT64", float(req.get("radius_km", 15)) * 1000),
        bigquery.ArrayQueryParameter("exclude", "STRING", req.get("exclude", [])),
        bigquery.ScalarQueryParameter("w_total", "FLOAT64", total),
        bigquery.ScalarQueryParameter("max_cells", "INT64", int(min(max(area_needed * 3, 60), 4000))),
        bigquery.ScalarQueryParameter("min_site_ha", "FLOAT64", MIN_SITE_HA),
        *[bigquery.ScalarQueryParameter(f"w_{k}", "FLOAT64", v) for k, v in weights.items()],
    ]
    ds = f"{config.PROJECT}.{config.BQ_DATASET}"
    rows = list(bq().query(SQL.format(ds=ds), job_config=bigquery.QueryJobConfig(query_parameters=params)).result())

    sites, picked = [], 0.0
    for r in rows:
        if picked >= area_needed:
            break
        picked += r.area_ha
        sites.append({
            "site_id": f"c{r.cluster_id}",
            "rank": len(sites) + 1,
            "geometry": json.loads(r.geojson),
            "centroid": [r.lon, r.lat],
            "area_ha": round(r.area_ha, 1),
            "final_score": round(r.final_score, 3),
            "breakdown": {k: round(getattr(r, k), 3) for k in WEIGHT_KEYS},
            "conditions": {k: round(getattr(r, k), 3) for k in (
                "slope_deg", "rain_annual_mm", "soil_moisture", "dist_forest_m", "dist_water_m",
                "pop_1km", "frac_bare", "frac_shrub", "frac_grass", "ndvi_dry", "ml_score")},
            "species": shortlist(r.slope_deg, r.frac_bare, r.dist_water_m, r.soil_moisture),
            "rationale": _rationale(r),
            "sources": [f"{ds}.grid_cells", f"{ds}.cell_predictions", "BQML establishment_model (Vertex AI Model Registry)"],
        })

    first = rows[0] if rows else None
    excluded = {e["excluded_reason"]: e["n"] for e in json.loads(first.excluded_json or "[]")} if first else {}
    return {
        "run_id": uuid.uuid4().hex[:12],
        "sites": sites,
        "area_ha_selected": round(picked, 1),
        "cells_considered": first.cells_considered if first else 0,
        "cells_excluded": excluded,
    }
