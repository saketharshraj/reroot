# ReRoot

Plant trees where they will survive, then prove they did. A hackathon build (Google Cloud,
Sustainability & Social Impact track) for forest departments, developers, NGOs and CSR teams
doing compensatory afforestation in India.

Flow: **Assess** (impact of a project footprint) → **Site** (rank candidate planting land) →
**Report** (Gemini-written plan) → **Prove** (monthly satellite monitoring, dry-spell alerts to
caretakers as voice notes, photo verification).

## Google Cloud

- Project `reroot-hack-2026`, region `us-central1`, BigQuery dataset `reroot` in **US**.
- gcloud: use the `reroot` configuration, never the user's default (it points at a production
  project). Prefix commands with `CLOUDSDK_ACTIVE_CONFIG_NAME=reroot` or pass `--project`.
- Runtime identity on Cloud Run: `app-runtime@reroot-hack-2026.iam.gserviceaccount.com`.
  Never create or commit service-account key files.
- Setup is `infra/setup.sh` (idempotent). Connectivity check is `scripts/smoke_test.py`.
- Deploy with `infra/deploy.sh [service] [module]`. API is live at
  https://api-152511810602.us-central1.run.app (`/health?deep=true` checks EE + Gemini).
- Cloud Run reserves URL paths ending in `z` (`/healthz` 404s at Google's front end). Don't use them.

## Layout and ownership (one agent per directory)

| Path | Owns | Notes |
|---|---|---|
| `src/reroot/core/` | config, credentials, clients, reusable EE layers | Shared; change with care |
| `src/reroot/pipelines/` | EE feature extraction → `reroot.grid_cells`, model training | Batch, uses standard EE endpoint |
| `src/reroot/api/` | FastAPI service. `agent.py` (Gemini + 22 tools, SSE), `assess`, `sites`, `plan`, `monitor`, `alerts`, `verify`, `basemap`, `policy` (trusted official sources), `explore` (place search, area analysis, timelapse), `datacenter` (water-positive siting + resource loops), `water` (replenishment verification + ledger) | Contract: `contracts/openapi.yaml` |
| `src/reroot/notifier/` | Pub/Sub push consumer → Telegram, Cloud TTS voice notes | Contract: `contracts/alert_message.schema.json` |
| `studio/` | **ReRoot Studio**: the product. Chat-first (Gemini agent) + map canvas, plan doc, monitor, caretaker phone | `VITE_API_BASE_URL`, `VITE_LAB_URL`; Firebase Hosting site `reroot-hack-2026` |
| `web/` | **Data Lab**: internal visualiser of layers, assess, site search | `VITE_API_BASE_URL`; Firebase Hosting site `reroot-lab` |
| `infra/` | setup and deploy scripts | |

Contracts in `contracts/` are the source of truth between agents. Change a contract first,
then the code on both sides.

## Rules

- Every number shown to a user carries `sources` (EE dataset IDs or BQ tables). Gemini may
  only cite numbers returned by tools; it never invents figures.
- Earth Engine is lazy and slow per round trip. Precompute anything the demo needs; cache
  tile URLs; never call `getInfo()` in a loop — build one `ee.FeatureCollection` and fetch once.
- Request-serving code uses `init_ee(high_volume=True)`; exports and batch jobs use the default.
- Exclude farmland, built-up, water/wetland, natural grassland, steep slopes and protected areas
  from planting candidates. Trees on grassland are an ecological harm, not a win.
- Gemini via `google-genai` with `vertexai=True` (see `core/clients.py`). Model IDs come from
  `core/config.py` env vars; don't hard-code them elsewhere.

## Pipeline

`src/reroot/pipelines/build_grid.py` builds the 100 m (1 ha) grid per region:
Earth Engine export -> `reroot.grid_raw_<region>` -> `reroot.grid_cells` (contract schema) ->
BQML boosted-tree `establishment_model` (registered in Vertex AI as `reroot_establishment`) ->
`reroot.cell_predictions`. Training: features from 2018, label = became tree cover by 2025.
Scoring: 2025 features. Holdout is spatial (2 km blocks) to avoid neighbour leakage.

## Agent and alerts

- `POST /v1/agent/chat` streams SSE events (`step`, `step_done`, `state`, `ui`, `text`, `done`). The
  server is stateless: Studio sends its workspace (profile, footprint, assessment, sites) each turn.
  Tools live in `api/agent.py`; add a tool = add a declaration in `TOOLS`, a label in `STEP_INFO`,
  and a method on `Tools` returning `(result_for_gemini, ui_events)`.
- Alerts: `/v1/monitor/run` publishes to Pub/Sub `alerts`; subscription `alerts-to-api` pushes
  (OIDC, `pubsub-invoker`) to Cloud Run `/pubsub/alerts`, which writes the Hindi/English message,
  the Cloud TTS voice note (GCS) and a BigQuery row, and mirrors the alert to Firestore `alerts`. Studio's caretaker
  phone subscribes to it with the Firebase SDK (`studio/src/firebase.ts`). Locally set `ALERTS_INLINE=1` to skip Pub/Sub.
- Plan maps crop `gs://reroot-hack-2026-data/basemaps/<region>.png` (render once with
  `uv run python -m reroot.api.basemap`). Demo plantations: `uv run python -m reroot.pipelines.seed_plantations`.
- Gemini numbers must come from tools. Survival odds are a percentile rank, never a probability.
- Policy answers come only from `research_policy`, which reads the hard-coded registry in
  `api/policy.py` (allow-listed government domains; live fetch, Cloud Storage snapshot fallback).
  Add sources there; refresh snapshots with `uv run python -m reroot.api.policy`.
- Replies link entities as `#site-N`, `#plantation-ID`, `#place-lon,lat`, `#layer-ID`, `#view-plan`,
  `#media`, `#area`, `#datacenter`, `#water-W1` (or `#water-adhoc`), `#view-water`; Studio turns these into map actions (unknown links are sent as a question).
- Studio keeps chat history in the browser's localStorage only (`studio/src/history.ts`).
- Regions: `core/regions.py` = built-in `config.REGIONS` + on-demand rows in BigQuery `reroot.regions`.
  `POST /v1/regions` (or the `load_region` tool) runs Cloud Run Job `region-builder`
  (`build_grid build --region X`: export, grid, score with the existing model, basemap) and reports
  progress; Studio polls `/v1/regions` and announces when ready. Satellite tools work anywhere;
  site search / planting potential need a ready region.
- Data centres: `api/datacenter.py` combines WRI Aqueduct 4.0, GRACE, ERA5-Land, CHIRPS and
  OpenStreetMap partners (cached per region in GCS `symbiosis/region/<id>.json`; Overpass is flaky,
  so mirrors are tried in turn). Cooling water uses latent-heat physics; assumptions are returned.
- Replenishment verification: `api/water.py`. Lake outline = JRC GSW max extent near the point; monthly
  open water = Sentinel-1 VV < -16 dB inside it (2018-now), with Dynamic World water and Sentinel-2
  NDVI > 0.5 green cover as the optical cross-check, and rainy-season rain (CHIRPS; ERA5-Land beyond
  50 deg). Seasons are automatic per lake: the wettest 4 months of its 1991-2020 rainfall climatology
  set the rains, the water year starts the month after, the peak window is the next 4 months from
  the second rainy month and the dry window the 4 months up to the next rains (Delhi = Jun-Sep rains,
  Aug-Nov peak, Mar-Jun dry; no clear rainy season = calendar years). Water years before vs after the
  intervention; the baseline peak is rain-scaled (elasticity 0.5, capped at the pre-project max). Verdicts: verified / partial / at_risk / not_additional /
  not_verified. Registry + series in BigQuery `water_projects` / `water_timeseries`
  (`uv run python -m reroot.pipelines.seed_water_projects`); ad-hoc lakes cached in GCS `water/series/`.
  Demo portfolio: sponsor and claims illustrative, satellite data real; Badkhal's revival is a real project.

## Deploy

See `DEPLOY.md`. Frontends: Studio on Firebase Hosting (rerootearth.com, reroot-hack-2026.web.app), Data Lab on
Firebase Hosting (reroot-lab.web.app); deploy both with `infra/deploy_firebase_hosting.py`. Backend: Cloud Run only (min 1 instance; `infra/deploy.sh` or GitHub Actions, no key).

## Commands

```
uv run python scripts/smoke_test.py            # check EE, BigQuery, Gemini, Pub/Sub
uv run python scripts/ee_what_you_can_get.py   # sample images/data → out/ee_demo/
uv run uvicorn reroot.api.main:app --reload --port 8080
uv run python -m reroot.pipelines.build_grid all   # rebuild grid + model for a region
npm run dev --prefix studio                        # Studio on :5174
npm run dev --prefix web                           # Data Lab on :5173
```
