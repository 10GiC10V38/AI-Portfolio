#!/bin/bash
# scripts/setup_gcp.sh
# Run this ONCE to bootstrap all GCP resources securely.
# Prerequisites: gcloud auth login

set -e

PROJECT_ID=$(gcloud config get-value project)
REGION="asia-south1"

echo "Setting up Portfolio AI on GCP project: ${PROJECT_ID}"
echo "Region: ${REGION}"
echo ""

# ── Enable required APIs ──────────────────────────────────────────────────────
echo "Enabling APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  containerregistry.googleapis.com

# ── Create least-privilege service account for agents ─────────────────────────
echo "Creating service account..."
gcloud iam service-accounts create portfolio-ai-agent-sa \
  --display-name="Portfolio AI Agent Service Account" \
  --description="Used by Cloud Run agents — least privilege"

SA_EMAIL="portfolio-ai-agent-sa@${PROJECT_ID}.iam.gserviceaccount.com"

# Grant ONLY what is needed — nothing more
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker"

# ── Create secrets in Secret Manager ─────────────────────────────────────────
echo ""
echo "Creating secrets in Secret Manager..."
echo "You will be prompted to enter each secret value."
echo ""

create_secret() {
  local name=$1
  local prompt=$2
  echo -n "${prompt}: "
  read -s value
  echo ""
  echo -n "${value}" | gcloud secrets create "${name}" \
    --data-file=- \
    --replication-policy=automatic
  # Grant agent SA access to this secret
  gcloud secrets add-iam-policy-binding "${name}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor"
  echo "Secret '${name}' created and access granted."
}

create_secret "gemini-api-key"       "Enter your Gemini API key (from aistudio.google.com)"
create_secret "anthropic-api-key"    "Enter your Anthropic API key (optional — press Enter to skip)"
create_secret "database-url"         "Enter your Neon PostgreSQL connection string"
create_secret "news-api-key"         "Enter your NewsAPI.org key (or press Enter to skip)"
create_secret "alpha-vantage-key"    "Enter your Alpha Vantage key (free at alphavantage.co)"
create_secret "youtube-api-key"      "Enter your YouTube Data API v3 key"
create_secret "scheduler-secret"     "Enter a random string for scheduler auth (e.g. run: openssl rand -hex 32)"
create_secret "jwt-secret"           "Enter a random string for JWT signing (e.g. run: openssl rand -hex 64)"

echo ""
echo "GCP setup complete."
echo ""
echo "Next steps:"
echo "1. Run the database schema: psql YOUR_NEON_URL < scripts/schema.sql"
echo "2. Build and push agents:   scripts/deploy_agents.sh"
echo "3. Create scheduler jobs:   infra/scheduler/create_jobs.sh"
