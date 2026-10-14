#!/usr/bin/env bash
# Deploy a Cloud Run service from this repo. Same image for every service.
#   infra/deploy.sh                 # api
#   infra/deploy.sh notifier reroot.notifier.main:app
set -euo pipefail
PROJECT="${PROJECT:-reroot-hack-2026}"
REGION="${REGION:-us-central1}"
SERVICE="${1:-api}"
MODULE="${2:-reroot.api.main:app}"
cd "$(dirname "$0")/.."
gcloud run deploy "$SERVICE" --source . --project="$PROJECT" --region="$REGION" \
  --service-account="app-runtime@${PROJECT}.iam.gserviceaccount.com" \
  --allow-unauthenticated --max-instances=3 --memory=1Gi \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT},SERVICE_MODULE=${MODULE}" --quiet
