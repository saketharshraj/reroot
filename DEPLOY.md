# Deploying ReRoot

## Live

| What | URL | How it deploys |
|---|---|---|
| ReRoot Studio (product) | https://rerootearth.com and https://reroot-hack-2026.web.app (Firebase Hosting) | `npm run build --prefix studio` then `CLOUDSDK_ACTIVE_CONFIG_NAME=reroot uv run python infra/deploy_firebase_hosting.py studio/dist` (Hosting REST API; no Firebase CLI) |
| ReRoot Studio on Vercel (copy) | https://reroot-studio-alpha.vercel.app | Vercel Git integration, on push to `main` touching `studio/` (root dir `studio`) |
| Data Lab (internal) | https://reroot-lab.vercel.app | Vercel Git integration, on push to `main` touching `web/` (root dir `web`) |
| API on Cloud Run (all frontends + the Pub/Sub alert worker; min 1 instance, 900 s timeout) | https://api-152511810602.us-central1.run.app | GitHub Actions `.github/workflows/deploy-cloudrun.yml` on push touching `src/`, `pyproject.toml`, `uv.lock`, `Dockerfile` (keyless, Workload Identity Federation) |

Pushes to other paths (docs, contracts) deploy nothing. Firebase Hosting is deployed by hand (command above),
so rebuild and run it after Studio changes. Manual API redeploy: `gh workflow run deploy-cloudrun.yml`.

Custom domain: `rerootearth.com` (registered at BigRock, renews Oct 2027) is a Firebase Hosting custom domain on
site `reroot-hack-2026`. DNS stays at BigRock: `A @ 199.36.158.100`, `TXT @ hosting-site=reroot-hack-2026`, and
`TXT _acme-challenge` (certificate). `www` is still `CNAME www a868125ccaabdd25.vercel-dns-017.com`: Vercel
308-redirects it to the apex. CORS on the API is `*`, so new frontend hosts need no API change.

The API ran on Render until 18 October 2026; that service is deleted. Render needed a service-account key because
it runs outside Google Cloud; Cloud Run doesn't.

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
