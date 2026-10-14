#!/usr/bin/env bash
# Idempotent Google Cloud setup for ReRoot. Safe to re-run.
#   infra/setup.sh                       # core setup
#   TEAMMATE=friend@gmail.com infra/setup.sh
# Uses explicit --project everywhere so it never touches your other gcloud projects.
set -uo pipefail

PROJECT="${PROJECT:-reroot-hack-2026}"
REGION="${REGION:-us-central1}"
BUCKET="${BUCKET:-${PROJECT}-data}"
RUNTIME_SA="app-runtime@${PROJECT}.iam.gserviceaccount.com"
INVOKER_SA="pubsub-invoker@${PROJECT}.iam.gserviceaccount.com"
G="gcloud --project=${PROJECT} --quiet"

say() { printf '\n== %s\n' "$*"; }

billing_on=$(gcloud billing projects describe "$PROJECT" --format='value(billingEnabled)' 2>/dev/null)
say "Billing enabled: ${billing_on:-False}"

say "APIs (no billing needed)"
$G services enable earthengine.googleapis.com bigquery.googleapis.com iam.googleapis.com \
  iamcredentials.googleapis.com aiplatform.googleapis.com pubsub.googleapis.com \
  vision.googleapis.com storage.googleapis.com analyticshub.googleapis.com

if [[ "$billing_on" == "True" ]]; then
  say "APIs (billing required)"
  $G services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    texttospeech.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com
else
  echo "Skipping Cloud Run, Cloud Build, Artifact Registry, Text-to-Speech, Secret Manager, Scheduler until billing is linked:"
  echo "  gcloud billing projects link ${PROJECT} --billing-account=<ACCOUNT_ID>"
fi

say "Service accounts"
$G iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1 || \
  $G iam service-accounts create app-runtime --display-name="Cloud Run runtime"
$G iam service-accounts describe "$INVOKER_SA" >/dev/null 2>&1 || \
  $G iam service-accounts create pubsub-invoker --display-name="Pub/Sub push invoker"

for r in roles/aiplatform.user roles/earthengine.writer roles/serviceusage.serviceUsageConsumer \
         roles/bigquery.dataEditor roles/bigquery.jobUser roles/storage.objectAdmin \
         roles/pubsub.publisher roles/secretmanager.secretAccessor; do
  $G projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${RUNTIME_SA}" \
    --role="$r" --condition=None >/dev/null && echo "  runtime SA has $r"
done

if [[ -n "${TEAMMATE:-}" ]]; then
  say "Teammate access for ${TEAMMATE}"
  $G projects add-iam-policy-binding "$PROJECT" --member="user:${TEAMMATE}" --role=roles/editor --condition=None >/dev/null \
    && echo "  ${TEAMMATE} is Editor"
fi

say "BigQuery dataset (US) and tables"
bq --project_id="$PROJECT" --location=US mk -d --description "ReRoot" "${PROJECT}:reroot" 2>/dev/null || echo "  dataset exists"
bq --project_id="$PROJECT" --location=US query --use_legacy_sql=false --quiet < "$(dirname "$0")/../contracts/bq_schema.sql" \
  && echo "  tables ensured"

say "Pub/Sub topic"
$G pubsub topics describe alerts >/dev/null 2>&1 || $G pubsub topics create alerts

if [[ "$billing_on" == "True" ]]; then
  say "Cloud Storage bucket"
  gcloud storage buckets describe "gs://${BUCKET}" --project="$PROJECT" >/dev/null 2>&1 || \
    gcloud storage buckets create "gs://${BUCKET}" --project="$PROJECT" --location=US --uniform-bucket-level-access
fi

say "Earth Engine registration"
echo "  If not done yet, register (noncommercial) here:"
echo "  https://console.cloud.google.com/earth-engine/configuration?project=${PROJECT}"
echo
echo "Done. Verify with: uv run python scripts/smoke_test.py"
