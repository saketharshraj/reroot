# Deploying ReRoot

## Live

| What | URL | How it deploys |
|---|---|---|
| ReRoot Studio (product) | https://rerootearth.com (also https://reroot-studio-alpha.vercel.app) | Vercel Git integration, on push to `main` touching `studio/` (root dir `studio`) |
| ReRoot Studio on Firebase Hosting (mirror) | https://reroot-hack-2026.web.app | `npm run build --prefix studio` then `CLOUDSDK_ACTIVE_CONFIG_NAME=reroot uv run python infra/deploy_firebase_hosting.py studio/dist` (Hosting REST API; no Firebase CLI) |
| Data Lab (internal) | https://reroot-lab.vercel.app | Vercel Git integration, on push to `main` touching `web/` (root dir `web`) |
| API on Cloud Run (primary: all three frontends + the Pub/Sub alert worker; min 1 instance, 900 s timeout) | https://api-152511810602.us-central1.run.app | GitHub Actions `.github/workflows/deploy-cloudrun.yml` on push touching `src/`, `pyproject.toml`, `uv.lock`, `Dockerfile` (keyless, Workload Identity Federation) |
| API on Render Starter (backup, not used by the frontends) | https://reroot-api.onrender.com | Render auto-deploy on the same backend paths |

Pushes to other paths (docs, contracts) deploy nothing.

Custom domain: `rerootearth.com` (registered at BigRock, renews Oct 2027) is attached to the Vercel project
`reroot-studio`; `www` 308-redirects to the apex. DNS stays at BigRock: `A @ 216.198.79.1`, `A @ 64.29.17.1`,
`CNAME www a868125ccaabdd25.vercel-dns-017.com`. CORS is `*`, so new frontend hosts need no API change. Manual redeploys: Vercel/Render dashboards
or `gh workflow run deploy-cloudrun.yml`. Both APIs read and write the same BigQuery/GCS/Firestore, so switching a frontend back to Render
(set `VITE_API_BASE_URL` and redeploy) needs nothing else.

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
It is the primary API. One instance is kept warm (`--min-instances=1`, set once with
`gcloud run services update`; deploys keep it), so the first request doesn't wait on a cold start,
and the 900 s request timeout leaves room for long agent streams.

## Frontends on Vercel (two projects, same repo)

| Project | Root directory | Env vars |
|---|---|---|
| **ReRoot Studio** (the product, for judges and buyers) | `studio` | `VITE_API_BASE_URL`, `VITE_LAB_URL` (the Data Lab URL) |
| **ReRoot Data Lab** (internal visualiser) | `web` | `VITE_API_BASE_URL` |

1. Vercel → **Add New → Project** → import `saketharshraj/reroot`.
2. Set **Root Directory** (`studio`, then repeat for `web`). Framework preset: Vite (auto-detected).
3. `VITE_API_BASE_URL` = `https://api-152511810602.us-central1.run.app`, no trailing slash.
4. Deploy. If you lock CORS down, add both Vercel URLs to the backend's `ALLOWED_ORIGINS`.


## Local

```bash
uv run uvicorn reroot.api.main:app --reload --port 8080
```
```bash
npm run dev --prefix web
```

## Firebase and Firestore

- Firebase is attached to `reroot-hack-2026`. Default Hosting site `reroot-hack-2026`; web app "ReRoot Studio".
  Its public web config is `VITE_FIREBASE_CONFIG` (Vercel env and `studio/.env.local`). It's not a secret: access is enforced by rules.
- Firestore (Native, `us-central1`) holds `alerts`, mirrored by the Pub/Sub worker, which the caretaker phone subscribes to,
  and `photo_verifications`. Rules: `firestore.rules`, released through the Firebase Rules API. `app-runtime` has `roles/datastore.user`.
- Don't use the local Firebase CLI for this project: it's logged in to a different (work) account. Use the gcloud
  `reroot` configuration and the REST APIs, as `infra/deploy_firebase_hosting.py` does.
