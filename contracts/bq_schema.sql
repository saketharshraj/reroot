-- BigQuery tables for ReRoot. Dataset `reroot`, location US (required for ST_REGIONSTATS
-- against Earth Engine assets). Owners: pipelines/ writes grid_cells; api/ writes the rest.

-- One row per 100 m (1 ha) grid cell in a region. Written by pipelines/build_grid.py.
-- "_t0" columns describe TRAIN_YEAR (2018); unsuffixed columns describe CURRENT_YEAR (2025).
CREATE TABLE IF NOT EXISTS reroot.grid_cells (
  cell_id STRING NOT NULL,          -- "<region>_<x>_<y>" (UTM grid index)
  region STRING NOT NULL,           -- e.g. "aravalli_ggn_fbd"
  geom GEOGRAPHY NOT NULL,          -- cell polygon
  centroid GEOGRAPHY NOT NULL,
  area_ha FLOAT64,
  -- Dynamic World land-cover fractions of the cell (0..1), current year
  frac_trees FLOAT64, frac_grass FLOAT64, frac_crops FLOAT64, frac_shrub FLOAT64,
  frac_built FLOAT64, frac_bare FLOAT64, frac_water FLOAT64,
  lc_class STRING,                  -- dominant class, current year
  -- same, training year
  frac_trees_t0 FLOAT64, frac_grass_t0 FLOAT64, frac_crops_t0 FLOAT64, frac_shrub_t0 FLOAT64,
  frac_built_t0 FLOAT64, frac_bare_t0 FLOAT64, frac_water_t0 FLOAT64,
  ndvi_dry FLOAT64,                 -- latest dry-season NDVI (Nov-Feb)
  slope_deg FLOAT64,
  elev_m FLOAT64,
  rain_annual_mm FLOAT64,           -- CHIRPS mean annual total over the last 6 years
  rain_cv FLOAT64,                  -- inter-annual coefficient of variation
  soil_moisture FLOAT64,            -- ERA5-Land top layer, pre-monsoon (Mar-May) mean, m3/m3
  dist_forest_m FLOAT64,            -- to nearest tree patch >= ~5 ha, current year (capped 3.84 km)
  dist_forest_m_t0 FLOAT64,
  dist_water_m FLOAT64,             -- to JRC surface water occurrence >= 50% (capped 3.84 km)
  pop_1km FLOAT64,                  -- people within 1 km (WorldPop 2020)
  is_protected BOOL,                -- inside a WDPA protected area
  embedding ARRAY<FLOAT64>,         -- AlphaEarth 64-d, current year, re-normalised to unit length
  embedding_t0 ARRAY<FLOAT64>,
  label_tree_gain INT64,            -- 1 = non-tree at t0 became tree-dominant by current year; 0 = stayed non-tree; NULL = not a training cell
  is_eval BOOL,                     -- spatial-block holdout for model evaluation
  train_year INT64,
  current_year INT64
)
CLUSTER BY region;

-- Establishment likelihood per cell from the Vertex-registered BQML model.
CREATE TABLE IF NOT EXISTS reroot.cell_predictions (
  cell_id STRING NOT NULL,
  region STRING NOT NULL,
  ml_score FLOAT64,                 -- P(becomes tree cover within ~7 years | current conditions)
  model STRING,
  created_at TIMESTAMP
)
CLUSTER BY region;

-- Output of a site search: scores per cell for one request.
CREATE TABLE IF NOT EXISTS reroot.candidate_scores (
  run_id STRING NOT NULL,
  cell_id STRING NOT NULL,
  excluded_reason STRING,           -- NULL if eligible; else "farmland" | "built" | "water" | "grassland" | "steep" | "protected"
  mcda_score FLOAT64,               -- transparent weighted score 0..1
  ml_score FLOAT64,                 -- establishment likelihood 0..1 (Vertex/BQML model)
  final_score FLOAT64,
  breakdown JSON,                   -- {"water": 0.8, "connectivity": 0.6, ...}
  created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reroot.plantations (
  plantation_id STRING NOT NULL,
  name STRING,
  geom GEOGRAPHY NOT NULL,
  planted_date DATE,
  species ARRAY<STRING>,
  saplings_planted INT64,
  caretaker_name STRING,
  caretaker_chat_id STRING,         -- Telegram chat id for alerts
  language STRING,                  -- BCP-47, e.g. "hi-IN"
  source_project STRING             -- which clearance / CSR project funded it
);

CREATE TABLE IF NOT EXISTS reroot.plantation_timeseries (
  plantation_id STRING NOT NULL,
  date DATE NOT NULL,
  ndvi_mean FLOAT64,
  ndvi_expected FLOAT64,            -- expected trajectory for a healthy plantation of this age
  sar_vh_mean FLOAT64,              -- Sentinel-1 backscatter, used when optical is cloudy
  soil_moisture FLOAT64,
  precip_last_14d_mm FLOAT64,
  precip_next_10d_mm FLOAT64        -- forecast
);

CREATE TABLE IF NOT EXISTS reroot.alerts (
  alert_id STRING NOT NULL,
  plantation_id STRING NOT NULL,
  created_at TIMESTAMP NOT NULL,
  type STRING,                      -- see contracts/alert_message.schema.json
  severity STRING,
  evidence JSON,
  message_en STRING,
  message_local STRING,
  delivered_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reroot.photo_verifications (
  verification_id STRING NOT NULL,
  plantation_id STRING,
  gcs_uri STRING NOT NULL,
  photo_lat FLOAT64,
  photo_lon FLOAT64,
  taken_at TIMESTAMP,
  location_match BOOL,              -- photo GPS falls inside the plantation polygon (+ tolerance)
  live_saplings_est INT64,
  dead_saplings_est INT64,
  confidence FLOAT64,
  notes STRING,
  model STRING,
  created_at TIMESTAMP
);
