#!/usr/bin/env bash
#
# One-command deploy of the SENTINEL backend to Google Cloud Run.
#
#   ./deploy/deploy.sh [PROJECT_ID] [REGION]
#
# Idempotent: every step tolerates already having been done, so re-running
# after a failure part-way through is safe and is the intended way to
# recover.
#
# Prerequisites you must do yourself (they cannot be scripted):
#   1. gcloud auth login
#   2. Billing enabled on the project - Cloud Run, Cloud Build and Artifact
#      Registry all refuse to even enable their APIs without it.
#
set -euo pipefail

PROJECT="${1:-${GCP_PROJECT_ID:-}}"
REGION="${2:-us-central1}"
SERVICE="sentinel-api"
REPO="sentinel"

if [[ -z "$PROJECT" ]]; then
  echo "usage: $0 PROJECT_ID [REGION]" >&2
  exit 2
fi

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

say "Targeting project $PROJECT in $REGION"
gcloud config set project "$PROJECT" >/dev/null

# --- Preflight: fail early and clearly on the one thing that blocks all of it
say "Checking billing"
if ! gcloud billing projects describe "$PROJECT" --format='value(billingEnabled)' 2>/dev/null | grep -qi true; then
  cat >&2 <<'BILLING'
ERROR: Billing is not enabled on this project.

Cloud Run, Cloud Build and Artifact Registry cannot even have their APIs
turned on without it, so nothing further will work.

Enable it at:
  https://console.cloud.google.com/billing

If you have hackathon credits, redeem them first - a billing account still
has to exist and be linked to this project, credits alone are not enough.
Then re-run this script.
BILLING
  exit 1
fi

say "Enabling APIs (this can take a couple of minutes the first time)"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  pubsub.googleapis.com \
  secretmanager.googleapis.com \
  cloudtrace.googleapis.com

say "Ensuring Artifact Registry repo '$REPO'"
gcloud artifacts repositories create "$REPO" \
  --repository-format=docker --location="$REGION" \
  --description="SENTINEL agent fleet images" 2>/dev/null \
  || echo "    (already exists)"

say "Ensuring Firestore database"
# nam5 is multi-region US. Change if you need data residency elsewhere.
gcloud firestore databases create --location=nam5 2>/dev/null \
  || echo "    (already exists)"

say "Ensuring Pub/Sub topic + subscription"
# The app also self-provisions these at runtime; doing it here as well means
# the resources exist before the first request rather than during it.
gcloud pubsub topics create sentinel-investigations 2>/dev/null \
  || echo "    (topic already exists)"
gcloud pubsub subscriptions create sentinel-investigations-worker \
  --topic=sentinel-investigations 2>/dev/null \
  || echo "    (subscription already exists)"

# --- Secrets ---------------------------------------------------------------
# Read from the local .env so keys are never typed on a command line (where
# they would land in shell history) and never baked into the image.
say "Syncing secrets to Secret Manager"
ENV_FILE="$(dirname "$0")/../backend/.env"

put_secret() {
  local name="$1" value="$2"
  [[ -z "$value" ]] && { echo "    skipping $name (not set locally)"; return; }
  if gcloud secrets describe "$name" >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- >/dev/null
    echo "    updated $name"
  else
    printf '%s' "$value" | gcloud secrets create "$name" --data-file=- >/dev/null
    echo "    created $name"
  fi
}

if [[ -f "$ENV_FILE" ]]; then
  GEMINI_KEY="$(grep -E '^GEMINI_API_KEY=' "$ENV_FILE" | cut -d= -f2- || true)"
  NUTRIENT_KEY="$(grep -E '^NUTRIENT_API_KEY=' "$ENV_FILE" | cut -d= -f2- || true)"
else
  GEMINI_KEY="${GEMINI_API_KEY:-}"
  NUTRIENT_KEY="${NUTRIENT_API_KEY:-}"
fi

put_secret gemini-api-key   "$GEMINI_KEY"
put_secret nutrient-api-key "$NUTRIENT_KEY"

# Cloud Run's runtime service account needs to read those secrets.
say "Granting the runtime service account access to secrets"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
for s in gemini-api-key nutrient-api-key; do
  gcloud secrets describe "$s" >/dev/null 2>&1 || continue
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role=roles/secretmanager.secretAccessor >/dev/null 2>&1 \
    || echo "    (binding for $s already present)"
done

# Firestore + Pub/Sub access for the same service account.
say "Granting Firestore + Pub/Sub access"
for role in roles/datastore.user roles/pubsub.editor; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${RUNTIME_SA}" --role="$role" >/dev/null 2>&1 \
    || echo "    (already has $role)"
done

# --- Build + deploy --------------------------------------------------------
say "Building and deploying (first build takes ~5-10 min)"
gcloud builds submit \
  --config "$(dirname "$0")/cloudbuild.yaml" \
  --substitutions=_REGION="$REGION",_SERVICE="$SERVICE",_REPO="$REPO" \
  "$(dirname "$0")/.."

URL="$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"

say "Deployed"
echo "  Service URL : $URL"
echo "  Health      : $URL/api/health"
echo "  API docs    : $URL/docs"
echo "  Console     : https://console.cloud.google.com/run/detail/$REGION/$SERVICE/metrics?project=$PROJECT"
echo
echo "Verifying it is actually serving..."
sleep 5
if curl -sf --max-time 90 "$URL/api/system-info" | python -m json.tool; then
  echo
  echo "SENTINEL is live on Cloud Run."
else
  echo "Service deployed but /api/system-info did not answer yet." >&2
  echo "First request cold-starts the container; retry, then check:" >&2
  echo "  gcloud run services logs read $SERVICE --region=$REGION --limit=50" >&2
fi
