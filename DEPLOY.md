# Deploying ReRoot

## Live

| What | URL | Deploys |
|---|---|---|
| ReRoot Studio (product) | https://reroot-studio-alpha.vercel.app | `vercel deploy --prod` in `studio/` |
| Data Lab (internal) | https://reroot-lab.vercel.app | `vercel deploy --prod` in `web/` |
| API on Cloud Run (primary) | https://api-152511810602.us-central1.run.app | `infra/deploy.sh` |
| API on Render (backup) | https://reroot-api.onrender.com | auto on push to `main` (needs the key, below) |

Frontend on **Vercel**, backend on **Render** (free) or **Cloud Run** (already live). Both
backends run the same Docker image.

## Backend on Render

Service `reroot-api` (free plan, Docker, Singapore) builds from `main` of
github.com/saketharshraj/reroot. Render runs outside Google Cloud, so it needs a
service-account key, mounted as a **Secret File**:

1. Create the key and copy it to the clipboard (the local file is deleted right away):
   ```bash
   gcloud iam service-accounts keys create /tmp/gcp-key.json --iam-account=app-runtime@reroot-hack-2026.iam.gserviceaccount.com --project=reroot-hack-2026 && pbcopy < /tmp/gcp-key.json && rm /tmp/gcp-key.json
   ```
2. Render dashboard → `reroot-api` → **Environment** → **Secret Files** → Add: filename
   `gcp-key.json`, paste the clipboard → Save, then **Manual Deploy → Deploy latest commit**.
3. Open `https://<service>.onrender.com/health?deep=true`. Expect `"ee": true` and `"gemini": "pong"`.
4. Once the Vercel URL exists, set `ALLOWED_ORIGINS` to it (comma-separate several).

`GOOGLE_APPLICATION_CREDENTIALS=/etc/secrets/gcp-key.json` is already set on the service.
(Alternatively put the key, raw or base64, in `GOOGLE_APPLICATION_CREDENTIALS_JSON`.)

Free-plan caveats: the service sleeps after 15 minutes idle and takes about a minute to wake,
and it has 512 MB of RAM. Open `/health` a few minutes before a demo. If the key leaks, delete
it in IAM → Service accounts → app-runtime → Keys.

## Backend on Cloud Run (already deployed)

```bash
infra/deploy.sh
```
URL: https://api-152511810602.us-central1.run.app. No key needed: it runs as `app-runtime`.
Cloud Run is in the hackathon's judged tech list and stays inside the free tier at demo
traffic, so it's a good primary with Render as the backup (or the other way round).

## Frontends on Vercel (two projects, same repo)

| Project | Root directory | Env vars |
|---|---|---|
| **ReRoot Studio** (the product, for judges and buyers) | `studio` | `VITE_API_BASE_URL`, `VITE_LAB_URL` (the Data Lab URL) |
| **ReRoot Data Lab** (internal visualiser) | `web` | `VITE_API_BASE_URL` |

1. Vercel → **Add New → Project** → import `saketharshraj/reroot`.
2. Set **Root Directory** (`studio`, then repeat for `web`). Framework preset: Vite (auto-detected).
3. `VITE_API_BASE_URL` = `https://api-152511810602.us-central1.run.app` (or the Render URL), no trailing slash.
4. Deploy. If you lock CORS down, add both Vercel URLs to the backend's `ALLOWED_ORIGINS`.

Alerts always flow through Cloud Run (Pub/Sub pushes there), even if Studio talks to Render.

## Local

```bash
uv run uvicorn reroot.api.main:app --reload --port 8080
```
```bash
npm run dev --prefix web
```
