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
| `src/reroot/api/` | FastAPI service, Gemini agent and its tools | Contract: `contracts/openapi.yaml` |
| `src/reroot/notifier/` | Pub/Sub push consumer → Telegram, Cloud TTS voice notes | Contract: `contracts/alert_message.schema.json` |
| `web/` | Frontend map app | Build against `fixtures/` until the API is live |
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

## Commands

```
uv run python scripts/smoke_test.py            # check EE, BigQuery, Gemini, Pub/Sub
uv run python scripts/ee_what_you_can_get.py   # sample images/data → out/ee_demo/
uv run uvicorn reroot.api.main:app --reload --port 8080
```
