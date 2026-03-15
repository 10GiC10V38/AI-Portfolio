#!/bin/bash
# scripts/deploy_agents.sh
# Build and deploy all 6 agents to Cloud Run in one command.
# Run after making changes to agent code.

set -e

PROJECT_ID=$(gcloud config get-value project)
REGION="asia-south1"

if [ -z "$PROJECT_ID" ]; then
  echo "ERROR: No GCP project set. Run: gcloud config set project YOUR_PROJECT_ID"
  exit 1
fi

echo "Deploying to project: $PROJECT_ID in $REGION"
echo ""

# Load secrets from GCP Secret Manager for env vars
echo "Loading secrets from GCP Secret Manager..."
DB_URL=$(gcloud secrets versions access latest --secret=database-url)
GEMINI_KEY=$(gcloud secrets versions access latest --secret=gemini-api-key)
NEWS_KEY=$(gcloud secrets versions access latest --secret=news-api-key 2>/dev/null || echo "")
YT_KEY=$(gcloud secrets versions access latest --secret=youtube-api-key 2>/dev/null || echo "")
AV_KEY=$(gcloud secrets versions access latest --secret=alpha-vantage-key 2>/dev/null || echo "")
SCHED_SECRET=$(gcloud secrets versions access latest --secret=scheduler-secret 2>/dev/null || echo "")

ENV_VARS="SECRETS_SOURCE=env,DATABASE_URL=$DB_URL,GEMINI_API_KEY=$GEMINI_KEY,NEWS_API_KEY=$NEWS_KEY,YOUTUBE_API_KEY=$YT_KEY,ALPHA_VANTAGE_KEY=$AV_KEY,SCHEDULER_SECRET=$SCHED_SECRET,LLM_PROVIDER=gemini,LOG_LEVEL=INFO"

AGENTS=("news" "fundamentals" "technical" "macro" "youtube" "advisor")

for agent in "${AGENTS[@]}"; do
  echo "── Deploying $agent agent ────────────────────"
  IMAGE="gcr.io/$PROJECT_ID/portfolio-ai-${agent}-agent:latest"
  SERVICE="portfolio-ai-${agent}-agent"

  # Generate a cloudbuild.yaml pointing to the correct Dockerfile, then submit repo root
  cat > /tmp/cloudbuild-${agent}.yaml << EOF
steps:
- name: 'gcr.io/cloud-builders/docker'
  args: ['build', '-t', '${IMAGE}', '-f', 'agents/${agent}/Dockerfile', '.']
images: ['${IMAGE}']
EOF

  gcloud builds submit . \
    --config "/tmp/cloudbuild-${agent}.yaml" \
    --quiet

  # Advisor gets different settings — on-demand HTTP server, not scheduler-triggered
  if [ "$agent" == "advisor" ]; then
    gcloud run deploy "$SERVICE" \
      --image "$IMAGE" \
      --region "$REGION" \
      --platform managed \
      --allow-unauthenticated \
      --memory 512Mi \
      --min-instances 0 \
      --max-instances 2 \
      --set-env-vars "$ENV_VARS" \
      --quiet
  else
    gcloud run deploy "$SERVICE" \
      --image "$IMAGE" \
      --region "$REGION" \
      --platform managed \
      --no-allow-unauthenticated \
      --memory 512Mi \
      --min-instances 0 \
      --max-instances 1 \
      --set-env-vars "$ENV_VARS" \
      --quiet
  fi

  URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format "value(status.url)")
  echo "  ✓ $agent deployed → $URL"
  echo ""
done

echo "All agents deployed."
echo ""
echo "Update infra/scheduler/create_jobs.sh with the URLs above, then run it."
