"""Build the 100 m feature grid for a region: Earth Engine -> BigQuery -> model.

    uv run python -m reroot.pipelines.build_grid probe        # sanity-check a 1 km box, no export
    uv run python -m reroot.pipelines.build_grid export       # EE batch export -> reroot.grid_raw_<region>
    uv run python -m reroot.pipelines.build_grid transform    # raw -> reroot.grid_cells (contract schema)
    uv run python -m reroot.pipelines.build_grid train        # BQML boosted trees, registered in Vertex AI
    uv run python -m reroot.pipelines.build_grid predict      # -> reroot.cell_predictions
    uv run python -m reroot.pipelines.build_grid all          # export (waits) + transform + train + predict
"""

import argparse
import json
import time

import ee

from reroot.core import config, layers as L
from reroot.core.clients import bq, init_ee

# Dynamic World label values for the classes we keep as per-cell fractions.
DW_FRACTIONS = {"trees": 1, "grass": 2, "crops": 4, "shrub": 5, "built": 6, "bare": 7, "water": 0}
EMB_BANDS = [f"A{i:02d}" for i in range(64)]
DIST_PX = 128
MAX_DIST_M = DIST_PX * 30  # fastDistanceTransform neighbourhood at 30 m


def _dw_label(region: ee.Geometry, year: int) -> ee.Image:
    return (ee.ImageCollection(L.DYNAMIC_WORLD).filterBounds(region)
            .filterDate(f"{year}-01-01", f"{year + 1}-01-01").select("label").mode())


def _dist_to_forest(region: ee.Geometry, year: int, proj10, proj30) -> ee.Image:
    """Metres to the nearest tree patch of at least ~5 ha (55 px at 30 m)."""
    trees30 = _dw_label(region, year).eq(1).reproject(proj30)
    patches = trees30.selfMask().connectedPixelCount(64, False).gte(55).unmask(0).reproject(proj30)
    return patches.fastDistanceTransform(DIST_PX).sqrt().multiply(30).unmask(MAX_DIST_M).min(MAX_DIST_M)


def feature_image(region_name: str) -> ee.Image:
    spec = config.REGIONS[region_name]
    aoi = L.bbox(spec["bbox"])
    buf = aoi.buffer(4000)  # so distances near the edge see features outside the box
    proj10 = ee.Projection(spec["utm"]).atScale(10)
    proj30 = ee.Projection(spec["utm"]).atScale(30)
    proj100 = ee.Projection(spec["utm"]).atScale(100)
    t0, cur = config.TRAIN_YEAR, config.CURRENT_YEAR

    def fractions(year: int, suffix: str) -> ee.Image:
        lab = _dw_label(buf, year)
        return ee.Image.cat([lab.eq(v).rename(f"frac_{k}{suffix}") for k, v in DW_FRACTIONS.items()])

    def emb(year: int, prefix: str) -> ee.Image:
        e = (ee.ImageCollection(L.EMBEDDINGS).filterBounds(buf)
             .filterDate(f"{year}-01-01", f"{year + 1}-01-01").mosaic())
        return e.select(EMB_BANDS, [f"{prefix}{b}" for b in EMB_BANDS])

    ndvi_dry = L.ndvi(L.s2_composite(buf, f"{cur}-11-01", f"{cur + 1}-03-01")).rename("ndvi_dry")

    # 10 m inputs: average into each 100 m cell.
    fine = (ee.Image.cat([fractions(t0, "_t0"), fractions(cur, ""), emb(t0, "e_t0_"), emb(cur, "e_"), ndvi_dry])
            .setDefaultProjection(proj10)
            .reduceResolution(ee.Reducer.mean(), maxPixels=1024)
            .reproject(proj100))

    dem_ic = ee.ImageCollection(L.DEM).filterBounds(buf).select("DEM")
    dem = dem_ic.mosaic().setDefaultProjection(dem_ic.first().projection())
    terrain = ee.Image.cat([dem.rename("elev_m"), ee.Terrain.slope(dem).rename("slope_deg")])

    years = range(cur - 5, cur + 1)
    chirps = ee.ImageCollection(L.CHIRPS)
    annual = ee.ImageCollection([chirps.filterDate(f"{y}-01-01", f"{y + 1}-01-01").sum() for y in years])
    rain_mean = annual.mean()
    rain = ee.Image.cat([rain_mean.rename("rain_annual_mm"),
                         annual.reduce(ee.Reducer.stdDev()).divide(rain_mean).rename("rain_cv")])

    soil = (ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR").filterDate(f"{cur}-03-01", f"{cur}-06-01")
            .select("volumetric_soil_water_layer_1").mean().rename("soil_moisture"))

    water30 = (ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").gte(50)
               .unmask(0).reproject(proj30))
    dist_water = water30.fastDistanceTransform(DIST_PX).sqrt().multiply(30).unmask(MAX_DIST_M).min(MAX_DIST_M)

    wp_ic = ee.ImageCollection("WorldPop/GP/100m/pop").filter(ee.Filter.eq("country", "IND")).filter(
        ee.Filter.eq("year", 2020))
    pop = (wp_ic.mosaic().setDefaultProjection(wp_ic.first().projection())
           .reduceNeighborhood(ee.Reducer.sum(), ee.Kernel.circle(1000, "meters")).rename("pop_1km"))

    protected = ee.Image(0).byte().paint(
        ee.FeatureCollection("WCMC/WDPA/current/polygons").filterBounds(buf), 1).rename("is_protected")

    smooth = ee.Image.cat([terrain, rain, soil]).resample("bilinear")
    coarse = ee.Image.cat([
        smooth,
        _dist_to_forest(buf, cur, proj10, proj30).rename("dist_forest_m"),
        _dist_to_forest(buf, t0, proj10, proj30).rename("dist_forest_m_t0"),
        dist_water.rename("dist_water_m"),
        pop,
        protected,
    ]).reproject(proj100)

    return fine.addBands(coarse)


def grid_features(region_name: str, geometry: ee.Geometry | None = None) -> ee.FeatureCollection:
    """One point per 100 m cell (at the cell centre) carrying every feature band.

    Uses image.sample, which Earth Engine tiles and streams. Building all cell polygons with
    coveringGrid + reduceRegions fails with "Computed value is too large" at ~80k cells; the
    squares are rebuilt in BigQuery instead (see transform)."""
    spec = config.REGIONS[region_name]
    proj100 = ee.Projection(spec["utm"]).atScale(100)
    area = geometry or L.bbox(spec["bbox"])
    index = ee.Image.pixelCoordinates(proj100).rename(["gx", "gy"]).reproject(proj100)  # cell centres, x.5
    return feature_image(region_name).addBands(index).sample(
        region=area, projection=proj100, geometries=True, dropNulls=False, tileScale=4)


def probe(region_name: str) -> None:
    lon, lat = config.DEMO_REFERENCE_POINT
    box = ee.Geometry.Point([lon + 0.02, lat + 0.005]).buffer(500).bounds()
    fc = grid_features(region_name, box)
    print("cells in 1 km box:", fc.size().getInfo())
    feats = fc.limit(3).getInfo()["features"]
    for f in feats:
        p = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in f["properties"].items()
             if not k.startswith("e_")}
        p["lonlat"] = [round(c, 5) for c in f["geometry"]["coordinates"]]
        print(json.dumps(p, sort_keys=True))
    e = [feats[0]["properties"].get(f"e_A{i:02d}") for i in range(64)]
    print("embedding present:", all(v is not None for v in e), "| norm:", round(sum(v * v for v in e) ** 0.5, 3))


def _tiles(bbox: list[float], n: int) -> list[list[float]]:
    w, so, e, no = bbox
    dx, dy = (e - w) / n, (no - so) / n
    return [[w + i * dx, so + j * dy, w + (i + 1) * dx, so + (j + 1) * dy] for i in range(n) for j in range(n)]


def export(region_name: str, n: int = 4, wait: bool = True) -> None:
    """Export the grid as n x n tiles appended to one BigQuery table.

    A single sample() over the whole region (~80k rows x 165 columns) fails with "Computed
    value is too large", so each tile is its own task. Tasks run in parallel on EE."""
    table = f"{config.PROJECT}.{config.BQ_DATASET}.grid_raw_{region_name}"
    bq().delete_table(table, not_found_ok=True)
    tasks = []
    for k, tile in enumerate(_tiles(config.REGIONS[region_name]["bbox"], n)):
        t = ee.batch.Export.table.toBigQuery(
            collection=grid_features(region_name, L.bbox(tile)),
            description=f"grid_{region_name}_{k:02d}", table=table, append=True)
        t.start()
        tasks.append(t)
    print(f"started {len(tasks)} EE export tasks -> {table}", flush=True)
    while wait:
        states = [t.status() for t in tasks]
        counts = {}
        for st in states:
            counts[st["state"]] = counts.get(st["state"], 0) + 1
        print(time.strftime("%H:%M:%S"), counts, flush=True)
        failed = [st for st in states if st["state"] in ("FAILED", "CANCELLED")]
        if failed:
            raise SystemExit(f"export FAILED: {failed[0].get('description')}: {failed[0].get('error_message')}")
        if all(st["state"] == "COMPLETED" for st in states):
            n_rows = bq().get_table(table).num_rows
            print(f"export COMPLETED: {n_rows} rows", flush=True)
            return
        time.sleep(30)


def _sql(statement: str, params: dict | None = None):
    from google.cloud import bigquery
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter(k, "STRING", v) for k, v in (params or {}).items()])
    return bq().query(statement, job_config=cfg).result()


def transform(region_name: str) -> None:
    ds = f"`{config.PROJECT}.{config.BQ_DATASET}`"
    classes = list(DW_FRACTIONS)
    frac = lambda s: ", ".join(f"frac_{k}{s}" for k in classes)  # noqa: E731
    arr = lambda p: "[" + ", ".join(f"{p}{b}" for b in EMB_BANDS) + "]"  # noqa: E731
    dominant = ", ".join(f"STRUCT('{k}' AS k, frac_{k} AS v)" for k in classes)
    _sql(f"""
CREATE TEMP FUNCTION unit(v ARRAY<FLOAT64>) AS ((
  SELECT ARRAY_AGG(x / NULLIF(SQRT((SELECT SUM(y * y) FROM UNNEST(v) y)), 0) ORDER BY o)
  FROM UNNEST(v) x WITH OFFSET o));

DELETE FROM {ds}.grid_cells WHERE region = @region;

INSERT INTO {ds}.grid_cells (
  cell_id, region, geom, centroid, area_ha, {frac('')}, lc_class, {frac('_t0')},
  ndvi_dry, slope_deg, elev_m, rain_annual_mm, rain_cv, soil_moisture,
  dist_forest_m, dist_forest_m_t0, dist_water_m, pop_1km, is_protected,
  embedding, embedding_t0, label_tree_gain, is_eval, train_year, current_year)
WITH raw AS (
  SELECT *, CAST(FLOOR(gx) AS INT64) AS ix, CAST(FLOOR(gy) AS INT64) AS iy
  FROM {ds}.grid_raw_{region_name} WHERE frac_trees IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY FLOOR(gx), FLOOR(gy)) = 1  -- tiles share edges
),
-- UTM cells are slightly rotated in lon/lat; measure the grid's step vectors from neighbours
-- so the rebuilt squares tile exactly.
step AS (
  SELECT
    (SELECT AVG(ST_X(b.geo) - ST_X(a.geo)) FROM raw a JOIN raw b ON b.ix = a.ix + 1 AND b.iy = a.iy) AS xx,
    (SELECT AVG(ST_Y(b.geo) - ST_Y(a.geo)) FROM raw a JOIN raw b ON b.ix = a.ix + 1 AND b.iy = a.iy) AS xy,
    (SELECT AVG(ST_X(b.geo) - ST_X(a.geo)) FROM raw a JOIN raw b ON b.iy = a.iy + 1 AND b.ix = a.ix) AS yx,
    (SELECT AVG(ST_Y(b.geo) - ST_Y(a.geo)) FROM raw a JOIN raw b ON b.iy = a.iy + 1 AND b.ix = a.ix) AS yy
),
cells AS (
  SELECT raw.*, ST_GEOGFROMTEXT(FORMAT('POLYGON((%f %f, %f %f, %f %f, %f %f, %f %f))',
    ST_X(geo) - (xx + yx) / 2, ST_Y(geo) - (xy + yy) / 2,
    ST_X(geo) + (xx - yx) / 2, ST_Y(geo) + (xy - yy) / 2,
    ST_X(geo) + (xx + yx) / 2, ST_Y(geo) + (xy + yy) / 2,
    ST_X(geo) - (xx - yx) / 2, ST_Y(geo) - (xy - yy) / 2,
    ST_X(geo) - (xx + yx) / 2, ST_Y(geo) - (xy + yy) / 2)) AS cell
  FROM raw, step
)
SELECT
  CONCAT(@region, '_', ix, '_', iy), @region, cell, geo, ST_AREA(cell) / 1e4,
  {frac('')},
  (SELECT k FROM UNNEST([{dominant}]) ORDER BY v DESC LIMIT 1),
  {frac('_t0')},
  ndvi_dry, slope_deg, elev_m, rain_annual_mm, rain_cv, soil_moisture,
  dist_forest_m, dist_forest_m_t0, dist_water_m, pop_1km, is_protected = 1,
  IF(e_A00 IS NULL, NULL, unit({arr('e_')})),
  IF(e_t0_A00 IS NULL, NULL, unit({arr('e_t0_')})),
  CASE WHEN frac_trees_t0 < 0.2 AND frac_built_t0 < 0.3 AND frac_water_t0 < 0.3 AND frac_built < 0.3
       THEN IF(frac_trees > 0.5, 1, 0) END,
  -- 2 km spatial blocks, every 5th block held out, so evaluation isn't flattered by neighbours
  MOD(DIV(ix, 20) + DIV(iy, 20), 5) = 0,
  {config.TRAIN_YEAR}, {config.CURRENT_YEAR}
FROM cells;
""", {"region": region_name})
    row = list(_sql(f"""
SELECT COUNT(*) n, COUNTIF(label_tree_gain = 1) pos, COUNTIF(label_tree_gain = 0) neg,
       COUNTIF(ARRAY_LENGTH(embedding) = 64) with_emb
FROM {ds}.grid_cells WHERE region = @region""", {"region": region_name}))[0]
    print(f"grid_cells[{region_name}]: {row.n} cells, labels {row.pos} gained / {row.neg} stayed, "
          f"{row.with_emb} with embeddings")


FEATURES = ["rain_annual_mm", "rain_cv", "soil_moisture", "slope_deg", "elev_m", "dist_water_m"]
TIME_FEATURES = ["dist_forest_m", "frac_grass", "frac_crops", "frac_shrub", "frac_bare"]


def _feature_select(t0: bool) -> str:
    sfx = "_t0" if t0 else ""
    cols = FEATURES + [f"{c}{sfx} AS {c}" for c in TIME_FEATURES]
    emb = "embedding_t0" if t0 else "embedding"
    cols += [f"{emb}[OFFSET({i})] AS e{i:02d}" for i in range(64)]
    return ",\n  ".join(cols)


def train(region_name: str) -> None:
    ds = f"`{config.PROJECT}.{config.BQ_DATASET}`"
    _sql(f"""
CREATE OR REPLACE VIEW {ds}.v_train AS
SELECT label_tree_gain AS label, is_eval,
  {_feature_select(t0=True)}
FROM {ds}.grid_cells WHERE label_tree_gain IS NOT NULL AND ARRAY_LENGTH(embedding_t0) = 64;

CREATE OR REPLACE VIEW {ds}.v_score AS
SELECT cell_id, region,
  {_feature_select(t0=False)}
FROM {ds}.grid_cells WHERE ARRAY_LENGTH(embedding) = 64;
""")
    print("training BQML boosted-tree model (registers in Vertex AI Model Registry)...", flush=True)
    _sql(f"""
CREATE OR REPLACE MODEL {ds}.establishment_model
OPTIONS (model_type = 'BOOSTED_TREE_CLASSIFIER', input_label_cols = ['label'],
         auto_class_weights = TRUE, data_split_method = 'CUSTOM', data_split_col = 'is_eval',
         max_iterations = 60, early_stop = TRUE,
         model_registry = 'VERTEX_AI', vertex_ai_model_id = 'reroot_establishment')
AS SELECT * FROM {ds}.v_train""")
    ev = list(_sql(f"SELECT * FROM ML.EVALUATE(MODEL {ds}.establishment_model)"))[0]
    print("holdout:", {k: round(v, 3) for k, v in dict(ev).items() if isinstance(v, float)})
    imp = _sql(f"""SELECT feature, ROUND(importance_gain, 1) gain FROM ML.FEATURE_IMPORTANCE(MODEL {ds}.establishment_model)
                   ORDER BY importance_gain DESC LIMIT 10""")
    print("top features:", [(r.feature, r.gain) for r in imp])


def predict(region_name: str) -> None:
    ds = f"`{config.PROJECT}.{config.BQ_DATASET}`"
    _sql(f"""
DELETE FROM {ds}.cell_predictions WHERE region = @region;
INSERT INTO {ds}.cell_predictions (cell_id, region, ml_score, model, created_at)
SELECT cell_id, region, (SELECT p.prob FROM UNNEST(predicted_label_probs) p WHERE p.label = 1),
       'establishment_model', CURRENT_TIMESTAMP()
FROM ML.PREDICT(MODEL {ds}.establishment_model, (SELECT * FROM {ds}.v_score WHERE region = @region));
""", {"region": region_name})
    row = list(_sql(f"""SELECT COUNT(*) n, ROUND(AVG(ml_score), 3) mean, ROUND(APPROX_QUANTILES(ml_score, 10)[OFFSET(9)], 3) p90
                        FROM {ds}.cell_predictions WHERE region = @region""", {"region": region_name}))[0]
    print(f"cell_predictions[{region_name}]: {row.n} cells, mean {row.mean}, p90 {row.p90}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["probe", "export", "transform", "train", "predict", "all"])
    ap.add_argument("--region", default=config.DEFAULT_REGION)
    a = ap.parse_args()
    if a.stage in ("probe", "export", "all"):
        init_ee()
    if a.stage == "probe":
        probe(a.region)
    if a.stage in ("export", "all"):
        export(a.region)
    if a.stage in ("transform", "all"):
        transform(a.region)
    if a.stage in ("train", "all"):
        train(a.region)
    if a.stage in ("predict", "all"):
        predict(a.region)


if __name__ == "__main__":
    main()
