# Deploying ReRoot

Frontend on **Vercel**, backend on **Render** (free) or **Cloud Run** (already live). Both
backends run the same Docker image.

## Backend on Render

Render runs outside Google Cloud, so it needs a service-account key. Create one for the
`app-runtime` account, copy it as base64, then delete the local file:

```bash
gcloud iam service-accounts keys create ~/reroot-render-key.json --iam-account=app-runtime@reroot-hack-2026.iam.gserviceaccount.com --project=reroot-hack-2026
```
```bash
base64 -i ~/reroot-render-key.json | pbcopy && rm ~/reroot-render-key.json
```

1. Push this repo to GitHub.
2. Render → **New → Blueprint** → pick the repo. It reads `render.yaml`.
3. When prompted, set:
   - `GOOGLE_APPLICATION_CREDENTIALS_JSON`: paste the base64 key from your clipboard.
   - `ALLOWED_ORIGINS`: your Vercel URL, e.g. `https://reroot.vercel.app` (comma-separate several).
4. After deploy, open `https://<service>.onrender.com/health?deep=true`. Expect `"ee": true` and `"gemini": "pong"`.

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

## Frontend on Vercel

1. Vercel → **Add New → Project** → import the repo.
2. **Root Directory:** `web`. Framework preset: Vite (auto-detected).
3. Environment variable `VITE_API_BASE_URL` = your backend URL (Render or Cloud Run), no trailing slash.
4. Deploy. Then add the Vercel URL to the backend's `ALLOWED_ORIGINS`.

## Local

```bash
uv run uvicorn reroot.api.main:app --reload --port 8080
```
```bash
npm run dev --prefix web
```
