# Deploying ReRoot

## Live

| What | URL | How it deploys |
|---|---|---|
| ReRoot Studio (product) | https://rerootearth.com and https://reroot-hack-2026.web.app (Firebase Hosting) | `npm run build --prefix studio` then `CLOUDSDK_ACTIVE_CONFIG_NAME=reroot uv run python infra/deploy_firebase_hosting.py studio/dist` (Hosting REST API; no Firebase CLI) |
| Data Lab (internal) | https://reroot-lab.web.app (Firebase Hosting site `reroot-lab`) | `npm run build --prefix web` then `CLOUDSDK_ACTIVE_CONFIG_NAME=reroot uv run python infra/deploy_firebase_hosting.py web/dist reroot-lab` |
| API on Cloud Run (all frontends + the Pub/Sub alert worker; min 1 instance, 900 s timeout) | https://api-152511810602.us-central1.run.app | GitHub Actions `.github/workflows/deploy-cloudrun.yml` on push touching `src/`, `pyproject.toml`, `uv.lock`, `Dockerfile` (keyless, Workload Identity Federation) |

Pushes to other paths (docs, contracts) deploy nothing. Firebase Hosting is deployed by hand (commands above),
so rebuild and run them after frontend changes. Build env comes from `studio/.env.local` and `web/.env.local`
(`VITE_API_BASE_URL` = the Cloud Run URL, `VITE_LAB_URL=https://reroot-lab.web.app`, `VITE_FIREBASE_CONFIG`). Manual API redeploy: `gh workflow run deploy-cloudrun.yml`.

Custom domain: `rerootearth.com` (registered at BigRock, renews Oct 2027) is a Firebase Hosting custom domain on
site `reroot-hack-2026`. DNS stays at BigRock: `A @ 199.36.158.100`, `TXT @ hosting-site=reroot-hack-2026`, and
`TXT _acme-challenge` (certificate). `www` is a Firebase custom domain that redirects to the apex:
`CNAME www reroot-hack-2026.web.app` plus `TXT _acme-challenge.www`. CORS on the API is `*`, so new frontend hosts need no API change.

Until 18 October 2026 the frontends were on Vercel and the API on Render; both are retired. Render needed a service-account key because
it runs outside Google Cloud; Cloud Run doesn't.

## Backend on Cloud Run (already deployed)

```bash
infra/deploy.sh
```
URL: https://api-152511810602.us-central1.run.app. No key needed: it runs as `app-runtime`.
It is the primary API. One instance is kept warm (`--min-instances=1`, set once with
`gcloud run services update`; deploys keep it), so the first request doesn't wait on a cold start,
and the 900 s request timeout leaves room for long agent streams.


## Local

```bash
uv run uvicorn reroot.api.main:app --reload --port 8080
```
```bash
npm run dev --prefix web
```

## Firebase and Firestore

- Firebase is attached to `reroot-hack-2026`. Hosting sites `reroot-hack-2026` (Studio, rerootearth.com) and `reroot-lab` (Data Lab); web app "ReRoot Studio".
  Its public web config is `VITE_FIREBASE_CONFIG` (`studio/.env.local`, baked in at build time). It's not a secret: access is enforced by rules.
- Firestore (Native, `us-central1`) holds `alerts`, mirrored by the Pub/Sub worker, which the caretaker phone subscribes to,
  and `photo_verifications`. Rules: `firestore.rules`, released through the Firebase Rules API. `app-runtime` has `roles/datastore.user`.
- Don't use the local Firebase CLI for this project: it's logged in to a different (work) account. Use the gcloud
  `reroot` configuration and the REST APIs, as `infra/deploy_firebase_hosting.py` does.
