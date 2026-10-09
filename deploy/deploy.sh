#!/usr/bin/env bash
# Deploy Forge to Google Cloud Run + Cloud SQL.
#
#   bash deploy/deploy.sh backend     # build + deploy the FastAPI service
#   bash deploy/deploy.sh frontend    # build + deploy the nginx + React service
#   bash deploy/deploy.sh all         # both, backend first (frontend needs its URL)
#
# Assumes, once per project:
#   gcloud auth login
#   gcloud config set project <PROJECT_ID>
#   gcloud services enable run sqladmin artifactregistry cloudbuild secretmanager
#   gcloud artifacts repositories create forge-images --repository-format=docker --location=$REGION
#   gcloud sql instances create forge-db ...            (see README)
#   deploy/bootstrap.sql run once as the postgres user  (creates forge_app)
#   the five secrets present in Secret Manager          (see SECRETS below)
#
# Everything here is idempotent: run it again after any code change.
set -euo pipefail

# Note for Windows: never pass a container path (e.g. /srv/workspace) as an
# argument from Git Bash - it rewrites POSIX-looking paths into Windows ones
# before gcloud sees them. Nothing here needs to; the paths live in the image.

PROJECT="$(gcloud config get-value project)"
REGION="${REGION:-asia-south1}"
REPO="forge-images"
SQL_INSTANCE="forge-db"
BACKEND="forge-backend"
FRONTEND="forge-frontend"
CONNECTION_NAME="${PROJECT}:${REGION}:${SQL_INSTANCE}"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"

# Secret Manager names -> the environment variables the app reads.
SECRETS="DATABASE_URL=forge-database-url:latest,\
GOOGLE_API_KEY=forge-google-api-key:latest,\
JWT_SECRET=forge-jwt-secret:latest,\
FORGE_MASTER_KEY=forge-master-key:latest,\
PLATFORM_ADMIN_PASSWORD=forge-platform-admin-password:latest"

say() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }

deploy_backend() {
  say "Building ${BACKEND} image"
  gcloud builds submit --config deploy/cloudbuild.backend.yaml \
    --substitutions=_REGION="${REGION}",_REPO="${REPO}",_IMAGE=backend .

  say "Deploying ${BACKEND} to Cloud Run"
  # --execution-environment=gen2: gives the container a writable (in-memory)
  #   filesystem, so the filesystem/git/sqlite stdio servers work inside
  #   /srv/workspace exactly as they do under docker compose. Without it the
  #   filesystem is read-only except /tmp and those three servers cannot start.
  # --min-instances=1 --no-cpu-throttling: the health sweep is an in-process
  #   asyncio task and SSE streams must stay alive between requests; a
  #   scaled-to-zero, CPU-throttled instance would kill both.
  # --max-instances=2: each instance keeps its own per-company database pools.
  # --timeout=3600: an agent run can wait on a human approval.
  gcloud run deploy "${BACKEND}" \
    --image="${IMAGE_BASE}/backend:latest" \
    --region="${REGION}" \
    --platform=managed \
    --allow-unauthenticated \
    --port=8080 \
    --cpu=1 --memory=2Gi \
    --min-instances=1 --max-instances=2 \
    --no-cpu-throttling \
    --execution-environment=gen2 \
    --timeout=3600 \
    --add-cloudsql-instances="${CONNECTION_NAME}" \
    --set-env-vars="HTTPS_ONLY=true,PLATFORM_ADMIN_EMAIL=admin@forge.dev" \
    --set-secrets="${SECRETS}" \
    --quiet

  gcloud run services describe "${BACKEND}" --region="${REGION}" --format='value(status.url)'
}

deploy_frontend() {
  local backend_url
  backend_url="$(gcloud run services describe "${BACKEND}" --region="${REGION}" --format='value(status.url)')"
  [ -n "${backend_url}" ] || { echo "deploy the backend first"; exit 1; }

  say "Building ${FRONTEND} image"
  gcloud builds submit --config deploy/cloudbuild.frontend.yaml \
    --substitutions=_REGION="${REGION}",_REPO="${REPO}",_IMAGE=frontend Frontend

  say "Deploying ${FRONTEND} to Cloud Run (proxying to ${backend_url})"
  gcloud run deploy "${FRONTEND}" \
    --image="${IMAGE_BASE}/frontend:latest" \
    --region="${REGION}" \
    --platform=managed \
    --allow-unauthenticated \
    --port=8080 \
    --cpu=1 --memory=512Mi \
    --min-instances=0 --max-instances=3 \
    --timeout=3600 \
    --set-env-vars="BACKEND_URL=${backend_url}" \
    --quiet

  say "Open the app"
  gcloud run services describe "${FRONTEND}" --region="${REGION}" --format='value(status.url)'
}

case "${1:-all}" in
  backend)  deploy_backend ;;
  frontend) deploy_frontend ;;
  all)      deploy_backend; deploy_frontend ;;
  *) echo "usage: bash deploy/deploy.sh [backend|frontend|all]"; exit 1 ;;
esac
