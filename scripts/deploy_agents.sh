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

AGENTS=("news" "fundamentals" "technical" "macro" "youtube" "advisor")

for agent in "${AGENTS[@]}"; do
  echo "── Deploying $agent agent ────────────────────"
  IMAGE="gcr.io/$PROJECT_ID/portfolio-ai-${agent}-agent:latest"
  SERVICE="portfolio-ai-${agent}-agent"

  # Build shared/ into the agent context
  # (Dockerfile copies ../../shared which needs to be in build context)
  TMPDIR=$(mktemp -d)
  cp -r "agents/$agent"/* "$TMPDIR/"
  cp -r "shared"          "$TMPDIR/shared"

  gcloud builds submit "$TMPDIR" \
    --tag "$IMAGE" \
    --quiet

  rm -rf "$TMPDIR"

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
      --set-env-vars "SECRETS_SOURCE=gcp,GCP_PROJECT_ID=$PROJECT_ID,LLM_PROVIDER=claude,LOG_LEVEL=INFO" \
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
      --set-env-vars "SECRETS_SOURCE=gcp,GCP_PROJECT_ID=$PROJECT_ID,LLM_PROVIDER=claude,LOG_LEVEL=INFO" \
      --quiet
  fi

  URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format "value(status.url)")
  echo "  ✓ $agent deployed → $URL"
  echo ""
done

echo "All agents deployed."
echo ""
echo "Update infra/scheduler/create_jobs.sh with the URLs above, then run it."
