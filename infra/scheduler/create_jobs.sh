#!/bin/bash
# infra/scheduler/create_jobs.sh
# Creates GCP Cloud Scheduler jobs for all polling agents.
# Run once after deploying agents.
# Replace <USER_ID> and <SERVICE_URL_*> with actual values.

set -e

PROJECT_ID=$(gcloud config get-value project)
REGION="asia-south1"
SCHEDULER_SECRET="${SCHEDULER_SECRET:?SCHEDULER_SECRET env var must be set}"

# Service URLs — deployed on GCP Cloud Run (asia-south1)
NEWS_URL="${NEWS_AGENT_URL:-https://portfolio-ai-news-agent-epdj46agqq-el.a.run.app}"
FUNDAMENTALS_URL="${FUNDAMENTALS_AGENT_URL:-https://portfolio-ai-fundamentals-agent-epdj46agqq-el.a.run.app}"
TECHNICAL_URL="${TECHNICAL_AGENT_URL:-https://portfolio-ai-technical-agent-epdj46agqq-el.a.run.app}"
MACRO_URL="${MACRO_AGENT_URL:-https://portfolio-ai-macro-agent-epdj46agqq-el.a.run.app}"
YOUTUBE_URL="${YOUTUBE_AGENT_URL:-https://portfolio-ai-youtube-agent-epdj46agqq-el.a.run.app}"

# Default user — in production this becomes a fan-out job per active user
USER_ID="${DEFAULT_USER_ID:?Set DEFAULT_USER_ID}"

BODY="{\"user_id\": \"$USER_ID\"}"

# Helper: create or update a scheduler job
create_or_update_job() {
  local NAME="$1" SCHEDULE="$2" TZ="$3" URI="$4" DEADLINE="$5"
  local HDRS="Content-Type=application/json,X-Scheduler-Secret=$SCHEDULER_SECRET"
  gcloud scheduler jobs create http "$NAME" \
    --location="$REGION" \
    --schedule="$SCHEDULE" \
    --time-zone="$TZ" \
    --uri="$URI" \
    --message-body="$BODY" \
    --headers="$HDRS" \
    --attempt-deadline="$DEADLINE" \
    --quiet 2>/dev/null || \
  gcloud scheduler jobs update http "$NAME" \
    --location="$REGION" \
    --schedule="$SCHEDULE" \
    --uri="$URI" \
    --message-body="$BODY" \
    --update-headers="$HDRS" \
    --quiet
}

echo "Creating Cloud Scheduler jobs in project: $PROJECT_ID"

# News agent — every 30 minutes, 9am–6pm IST, Mon-Fri
create_or_update_job "portfolio-ai-news" "*/30 3-13 * * 1-5" "Asia/Kolkata" "$NEWS_URL/run" "90s"
echo "  ✓ news agent job created"

# Fundamentals agent — every 6 hours
create_or_update_job "portfolio-ai-fundamentals" "0 */6 * * *" "Asia/Kolkata" "$FUNDAMENTALS_URL/run" "300s"
echo "  ✓ fundamentals agent job created"

# Technical agent — every 30 minutes, 9am–4pm IST, Mon-Fri
create_or_update_job "portfolio-ai-technical" "*/30 3-11 * * 1-5" "Asia/Kolkata" "$TECHNICAL_URL/run" "120s"
echo "  ✓ technical agent job created"

# Macro agent — once daily at 7am IST (1:30 UTC)
create_or_update_job "portfolio-ai-macro" "30 1 * * *" "UTC" "$MACRO_URL/run" "300s"
echo "  ✓ macro agent job created"

# YouTube agent — every 6 hours
create_or_update_job "portfolio-ai-youtube" "0 2,8,14,20 * * *" "UTC" "$YOUTUBE_URL/run" "300s"
echo "  ✓ youtube agent job created"

echo ""
echo "All scheduler jobs created. Verify with:"
echo "  gcloud scheduler jobs list --location=$REGION"
