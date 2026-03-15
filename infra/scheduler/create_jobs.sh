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
HEADERS="Content-Type:application/json,X-Scheduler-Secret:$SCHEDULER_SECRET"

echo "Creating Cloud Scheduler jobs in project: $PROJECT_ID"

# News agent — every 30 minutes, 9am–6pm IST (3:30am–12:30pm UTC), Mon-Fri
gcloud scheduler jobs create http portfolio-ai-news \
  --location="$REGION" \
  --schedule="*/30 3-13 * * 1-5" \
  --time-zone="Asia/Kolkata" \
  --uri="$NEWS_URL/run" \
  --message-body="$BODY" \
  --headers="$HEADERS" \
  --attempt-deadline=90s \
  --quiet 2>/dev/null || \
gcloud scheduler jobs update http portfolio-ai-news \
  --location="$REGION" \
  --schedule="*/30 3-13 * * 1-5" \
  --uri="$NEWS_URL/run" \
  --message-body="$BODY" \
  --headers="$HEADERS" \
  --quiet
echo "  ✓ news agent job created"

# Fundamentals agent — every 6 hours
gcloud scheduler jobs create http portfolio-ai-fundamentals \
  --location="$REGION" \
  --schedule="0 */6 * * *" \
  --time-zone="Asia/Kolkata" \
  --uri="$FUNDAMENTALS_URL/run" \
  --message-body="$BODY" \
  --headers="$HEADERS" \
  --attempt-deadline=300s \
  --quiet 2>/dev/null || \
gcloud scheduler jobs update http portfolio-ai-fundamentals \
  --location="$REGION" \
  --schedule="0 */6 * * *" \
  --uri="$FUNDAMENTALS_URL/run" \
  --message-body="$BODY" \
  --headers="$HEADERS" \
  --quiet
echo "  ✓ fundamentals agent job created"

# Technical agent — every 30 minutes, 9am–4pm IST, Mon-Fri
gcloud scheduler jobs create http portfolio-ai-technical \
  --location="$REGION" \
  --schedule="*/30 3-11 * * 1-5" \
  --time-zone="Asia/Kolkata" \
  --uri="$TECHNICAL_URL/run" \
  --message-body="$BODY" \
  --headers="$HEADERS" \
  --attempt-deadline=120s \
  --quiet 2>/dev/null || \
gcloud scheduler jobs update http portfolio-ai-technical \
  --location="$REGION" \
  --schedule="*/30 3-11 * * 1-5" \
  --uri="$TECHNICAL_URL/run" \
  --message-body="$BODY" \
  --headers="$HEADERS" \
  --quiet
echo "  ✓ technical agent job created"

# Macro agent — once daily at 7am IST
gcloud scheduler jobs create http portfolio-ai-macro \
  --location="$REGION" \
  --schedule="30 1 * * *" \
  --time-zone="UTC" \
  --uri="$MACRO_URL/run" \
  --message-body="$BODY" \
  --headers="$HEADERS" \
  --attempt-deadline=300s \
  --quiet 2>/dev/null || \
gcloud scheduler jobs update http portfolio-ai-macro \
  --location="$REGION" \
  --schedule="30 1 * * *" \
  --uri="$MACRO_URL/run" \
  --message-body="$BODY" \
  --headers="$HEADERS" \
  --quiet
echo "  ✓ macro agent job created"

# YouTube agent — every 6 hours
gcloud scheduler jobs create http portfolio-ai-youtube \
  --location="$REGION" \
  --schedule="0 2,8,14,20 * * *" \
  --time-zone="UTC" \
  --uri="$YOUTUBE_URL/run" \
  --message-body="$BODY" \
  --headers="$HEADERS" \
  --attempt-deadline=300s \
  --quiet 2>/dev/null || \
gcloud scheduler jobs update http portfolio-ai-youtube \
  --location="$REGION" \
  --schedule="0 2,8,14,20 * * *" \
  --uri="$YOUTUBE_URL/run" \
  --message-body="$BODY" \
  --headers="$HEADERS" \
  --quiet
echo "  ✓ youtube agent job created"

echo ""
echo "All scheduler jobs created. Verify with:"
echo "  gcloud scheduler jobs list --location=$REGION"
