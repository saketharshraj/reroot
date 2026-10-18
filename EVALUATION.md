# Evaluation

What we measured, how, and the limits we know about. Numbers are from the live system on 16-18 October 2026.

## 1. Survival model (where saplings will establish)

**Question:** given what a one-hectare cell looked like in 2018, did it become tree cover by 2025?

- **Data:** the 100 m grid over the Aravalli belt (Gurugram-Faridabad), 81,453 cells. Features include:
  - 2018 AlphaEarth satellite embeddings (64-d);
  - Sentinel-2 NDVI;
  - Dynamic World land cover fractions;
  - terrain;
  - CHIRPS rainfall;
  - SMAP soil moisture;
  - distance to water;
  - population.

  The label is a tree-cover gain by 2025 (Dynamic World).
- **Model:** BigQuery ML boosted-tree classifier, class-weighted and registered in the Vertex AI Model Registry as
  `reroot_establishment`.
- **Holdout:** spatial. The region is cut into 2 km blocks, and every 5th block is held out, so neighbouring cells
  can't leak between train and test. 18,081 training cells (19.8% positive) and 3,693 holdout cells (16.4% positive).

| Metric (spatial holdout) | Value |
|---|---|
| ROC AUC | **0.959** |
| Accuracy | 0.884 |
| Recall | 0.924 |
| Precision | 0.714 |
| F1 | 0.806 |
| Log loss | 0.259 |

The top features by gain are AlphaEarth embedding dimensions (e07, e35, e20, e30), then soil moisture, annual
rainfall and crop fraction.

**How it's used:** the score ranks eligible cells, and the UI calls it a *survival-odds percentile*
("better than 97% of nearby land"), never a probability. Candidates exclude farmland, built-up land, water,
grassland, steep slopes, protected areas and the project footprint itself. Sites are DBSCAN clusters of good cells
(at least 5 ha). The model was trained on the Aravalli belt. Regions built on demand reuse it, and the agent calls their
ranking *indicative*.

## 2. Impact assessment (demo bypass)

14 km × 100 m footprint: 144.8 ha in total, of which 106.1 ha is tree cover (Dynamic World). That's 3,541 t CO₂e of
biomass carbon (GEDI L4B and Meta canopy height), so at least 110.3 ha must be replanted on non-forest land, or 220.5 ha if
on degraded forest land. The site search scored 78,431 cells and excluded 23,041 that are already forest, 10,007 farmland
and 332 inside the footprint. It returned 11 sites, 115 ha.

## 3. Plantation monitoring and alerts

- **Health index:** recent Sentinel-2 NDVI divided by the NDVI expected for a plantation of that age. The expected
  trajectory comes from a nearby mature forest, ramped over 5 years. Bands: on track ≥ 0.9, watch ≥ 0.75, otherwise at risk.
  The demo's three plantations use real satellite history: two are cells that genuinely became forest, one is scrub
  that didn't. They come out on track (0.91), watch (0.80) and at risk (0.75).
- **Weather risk:** NOAA GFS 10-day forecast, GSMaP 14-day rain and SMAP soil moisture against normal.
- **End to end:**
  - `/v1/monitor/run` publishes to Pub/Sub;
  - the push worker on Cloud Run has Gemini write the Hindi and English message, and Text-to-Speech makes the voice note;
  - the alert is written to BigQuery, Cloud Storage and **Firestore**, and appears on the caretaker phone via the Firebase SDK.

  Measured: **about 10 s from publish to the Firestore document**. The phone needs no refresh.
- **Photo verification:** Gemini vision estimates live and dead saplings and checks the photo's GPS against the plantation
  polygon. Results are logged to BigQuery and Firestore.

## 4. Water replenishment verification

- **Method:**
  - **Lake outline:** the JRC Global Surface Water maximum extent.
  - **Monthly open water, 2018 to now:** Sentinel-1 VV < −16 dB inside that outline.
  - **Optical cross-check:** Dynamic World water and Sentinel-2 dense green cover.
  - **Rain:** CHIRPS, or ERA5-Land beyond 50° latitude.
  - **Seasons:** taken from each lake's own 1991-2020 rainfall.
  - **Comparison:** the water years before vs after the works. The baseline is scaled by rain (elasticity 0.5, capped at
    the pre-project maximum), so a wet year can't pass for a working project.

| Lake | Verdict | Evidence |
|---|---|---|
| Badkhal (Faridabad; real revival) | **At risk** | Dry before; water back in 2023-24; by 2025, 25 of 32 ha is dense green cover. Radar and optical agree in only 35% of months, consistent with weeds on water. |
| Najafgarh Jheel | **Verified** | Peak above the rain-expected level every year since 2021 (e.g. 380 ha vs 238 ha expected); dry-season water up too. |
| Damdama | **Not verified** | A 2022-23 gain didn't last; recent peaks match what rainfall explains. |
| Neeli Jheel | **Not additional** | Permanent water before the project; no gain to credit. |
| Porur, Chennai (ad-hoc check) | Partial | Its own Oct-Sep water year (north-east monsoon) found automatically; peaks track the heavy 2021 monsoon. |

- **Automatic seasons, checked across climates:**

  | Place | Rains | Fullest |
  |---|---|---|
  | Delhi | Jun-Sep | Aug-Nov |
  | Chennai | Sep-Dec | Nov-Feb |
  | Bengaluru | Jul-Oct | Sep-Dec |
  | Cape Town | May-Aug | Jul-Oct |
  | Singapore, Stockholm | no clear rainy season | calendar years |

- **Credited volume** is indicative: extra water area × an assumed depth. Satellites can't see depth or quality.
- **Limits:**
  - ponds under about 2 ha are near the 10 m resolution limit;
  - radar can read smooth ground (runways, graded beds) as water, which is why measurement stays inside the outline;
  - frozen lakes and snowmelt aren't modelled.

## 5. Data-centre water screening

- **Cooling water** from latent-heat physics: 3.6 / 2.45 L/kWh of evaporation, times (1 + 1 / (cycles − 1)) with
  4 cycles of concentration. Three designs: evaporative; hybrid, using hours above 25 °C; and closed loop.
- **Cooling hours** use a daily min/max sinusoidal approximation of ERA5-Land. It is within **0.5%** of the
  8,760-hour count and about 7× faster (5.8 s vs 40 s).
- **Gwal Pahari, 50 MW, evaporative:**
  - water stress: extremely high;
  - groundwater: −3.4 cm/yr;
  - cooling water: 944 ML/yr;
  - treated wastewater from a sewage treatment plant 9.9 km away covers 70% of make-up water;
  - 120% replenishment target: 340 ML/yr.
- **Partners** come from OpenStreetMap. Heat users are limited to 5 km, and depots are filtered out. All assumptions are returned with the result.

## 6. Agent grounding and safety

- Numbers come only from tool results. Every result carries its `sources` (Earth Engine dataset IDs or BigQuery tables).
- **Policy and legal questions** go only through `research_policy`, which reads an allow-listed registry of
  government sources (Van Rules 2023, CAF Act 2016, Green Credit Programme, Aravalli Green Wall, ISFR 2023). It fetches
  them live, falls back to a Cloud Storage snapshot, and flags each source as live, cached or TLS-unverified.
- **Prompt fixes from failures we saw in testing:**
  - "97% survival probability" became a percentile rank;
  - pond volumes invented while closing a replenishment gap now come only from the screening tool;
  - comma decimals became dot decimals;
  - an unasked ledger call during a plain data-centre screening was removed.

## 7. How we tested

Almost everything ReRoot does depends on live satellite data, BigQuery and Gemini, so we test end to end against
the real services rather than against mocks. Every feature is checked this way:
- `scripts/smoke_test.py` (Earth Engine, BigQuery, Gemini, Pub/Sub) and `scripts/api_check.py` (API endpoints);
- `/health?deep=true` on each deploy (GitHub Actions smoke test for Cloud Run);
- scripted end-to-end runs of every demo flow against the live site. The demo video is one such run, recorded with
  Playwright, and its narration's numbers were checked against the recorded output;
- manual verification of each satellite claim above against Sentinel-2 imagery: the Badkhal frames, Najafgarh and the
  bypass footprint.
