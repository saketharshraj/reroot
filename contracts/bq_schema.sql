-- BigQuery tables for ReRoot. Dataset `reroot`, location US (required for ST_REGIONSTATS
-- against Earth Engine assets). Owners: pipelines/ writes grid_cells; api/ writes the rest.

-- One row per ~100 m grid cell in a region, with the features the scorer and model use.
CREATE TABLE IF NOT EXISTS reroot.grid_cells (
  cell_id STRING NOT NULL,          -- "<region>_<row>_<col>"
  region STRING NOT NULL,           -- e.g. "aravalli_ggn_fbd"
  geom GEOGRAPHY NOT NULL,          -- cell polygon
  centroid GEOGRAPHY NOT NULL,
  lc_class STRING,                  -- Dynamic World mode class name for the feature year
  tree_prob FLOAT64,                -- Dynamic World mean tree probability 0..1
  ndvi_dry FLOAT64,                 -- dry-season NDVI
  slope_deg FLOAT64,
  elev_m FLOAT64,
  rain_annual_mm FLOAT64,           -- CHIRPS mean annual, last 5 years
  rain_cv FLOAT64,                  -- inter-annual coefficient of variation
  soil_moisture FLOAT64,            -- dry-season mean, m3/m3
  dist_forest_m FLOAT64,            -- distance to nearest tree-cover patch
  dist_water_m FLOAT64,
  pop_1km FLOAT64,                  -- people within 1 km (WorldPop)
  is_protected BOOL,
  embedding ARRAY<FLOAT64>,         -- AlphaEarth 64-d, feature year
  label_tree_gain INT64,            -- 1 if non-tree -> tree between label years, else 0; NULL if not a training cell
  feature_year INT64
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
