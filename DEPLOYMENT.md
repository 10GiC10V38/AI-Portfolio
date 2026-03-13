# Portfolio AI — Deployment Guide
# From zero to live in ~1 hour. Phase 1: completely free except Claude API.

---

## Prerequisites
- Google Cloud account (free tier — no credit card needed for our usage)
- Neon account — https://neon.tech (free tier, no credit card)
- Vercel account — https://vercel.com (free tier)
- GitHub account (for auto-deploy on git push)
- gcloud CLI — https://cloud.google.com/sdk/docs/install
- Node.js 20+, Python 3.12+, Go 1.22+, Docker

---

## Step 1 — Local setup (15 min)

```bash
# Clone and enter the project
git clone https://github.com/YOUR_USERNAME/portfolio-ai.git
cd portfolio-ai

# Create your .env from the template
cp .env.example .env
# Edit .env — fill in your API keys

# Start all services locally
docker-compose -f infra/docker/docker-compose.yml up --build
```

Verify everything is up:
- http://localhost:3000      → frontend
- http://localhost:8080/health → API gateway
- http://localhost:8002/health → news agent

---

## Step 2 — Neon PostgreSQL (5 min)

1. Go to https://console.neon.tech and create a project
2. Name it "portfolio-ai"
3. Copy the connection string — looks like:
   `postgresql://user:password@ep-xxxx.us-east-2.aws.neon.tech/neondb?sslmode=require`
4. Run the schema:
   ```bash
   psql "YOUR_NEON_CONNECTION_STRING" < scripts/schema.sql
   ```
5. Verify: `psql "YOUR_NEON_STRING" -c "\dt"` — should list 10 tables

---

## Step 3 — GCP setup (15 min)

```bash
# Install gcloud CLI and log in
gcloud auth login
gcloud projects create portfolio-ai-YOUR-NAME
gcloud config set project portfolio-ai-YOUR-NAME

# Run the setup script — it creates IAM, service account, and all secrets
chmod +x scripts/setup_gcp.sh
./scripts/setup_gcp.sh
```

The script will prompt for each secret. Have these ready:
- Anthropic API key (https://console.anthropic.com)
- Neon connection string (from Step 2)
- NewsAPI key (https://newsapi.org — free)
- Alpha Vantage key (https://alphavantage.co — free)
- YouTube Data API v3 key (https://console.cloud.google.com → APIs → YouTube Data API v3)
- A random scheduler secret: `openssl rand -hex 32`
- A random JWT secret: `openssl rand -hex 64`

---

## Step 4 — Deploy agents to Cloud Run (15 min)

```bash
# Build and push each agent image
PROJECT_ID=$(gcloud config get-value project)

for agent in news fundamentals technical macro youtube advisor; do
  echo "Building $agent..."
  gcloud builds submit ./agents/$agent \
    --tag gcr.io/$PROJECT_ID/portfolio-ai-$agent-agent:latest
  
  gcloud run deploy portfolio-ai-$agent-agent \
    --image gcr.io/$PROJECT_ID/portfolio-ai-$agent-agent:latest \
    --region asia-south1 \
    --platform managed \
    --no-allow-unauthenticated \
    --memory 512Mi \
    --set-env-vars SECRETS_SOURCE=gcp,GCP_PROJECT_ID=$PROJECT_ID,LLM_PROVIDER=claude
done
```

Note: `--no-allow-unauthenticated` = only Cloud Scheduler can trigger agents.

---

## Step 5 — Set up Cloud Scheduler (5 min)

1. Open `infra/scheduler/create_jobs.sh`
2. Fill in the Cloud Run URLs printed after Step 4
3. Fill in your USER_ID (create an account first in Step 7, then get the UUID)
4. Run it:
   ```bash
   chmod +x infra/scheduler/create_jobs.sh
   ./infra/scheduler/create_jobs.sh
   ```

---

## Step 6 — Deploy API gateway to Render.com (5 min)

1. Go to https://render.com → New → Web Service
2. Connect your GitHub repo
3. Configure:
   - Root directory: `api`
   - Runtime: Docker
   - Set environment variables:
     - `DATABASE_URL` = your Neon string
     - `JWT_SECRET` = same value you put in Secret Manager
     - `ADVISOR_URL` = Cloud Run URL for advisor agent
     - `ENVIRONMENT` = production
4. Click Deploy
5. Copy your Render URL (e.g. `https://portfolio-ai-api.onrender.com`)

Note: Render free tier sleeps after 15 min. Acceptable for a personal app —
first request after idle takes ~10 seconds to cold start.

---

## Step 7 — Deploy frontend to Vercel (5 min)

```bash
cd frontend

# Create .env.production
echo "VITE_API_URL=https://portfolio-ai-api.onrender.com" > .env.production
```

1. Go to https://vercel.com → New Project
2. Import your GitHub repo
3. Configure:
   - Framework: Vite
   - Root directory: `frontend`
   - Environment variable: `VITE_API_URL` = your Render URL
4. Click Deploy
5. Your app is live at `https://your-app.vercel.app`

---

## Step 8 — Create your account and add holdings (5 min)

1. Open your Vercel URL
2. Click "Create account" and register
3. Copy your user_id from the login response (check browser DevTools → Network)
4. Add your holdings to the database:
   ```sql
   INSERT INTO holdings (user_id, ticker, exchange, company_name, sector, quantity, avg_cost, currency)
   VALUES
     ('YOUR-UUID', 'RELIANCE', 'NSE', 'Reliance Industries', 'Energy',      50, 2450.00, 'INR'),
     ('YOUR-UUID', 'INFY',     'NSE', 'Infosys Ltd',          'Technology',  100, 1750.00, 'INR'),
     ('YOUR-UUID', 'HDFCBANK', 'NSE', 'HDFC Bank',            'Financials',  75, 1600.00, 'INR');
   ```
5. Update `infra/scheduler/create_jobs.sh` with your real USER_ID and re-run

---

## Step 9 — Verify everything is working

```bash
# Trigger the news agent manually (replace URL with your Cloud Run URL)
curl -X POST https://YOUR-AGENT-URL.run.app/run \
  -H "Content-Type: application/json" \
  -d '{"user_id": "YOUR-UUID"}'
# Should return: {"status": "success", "alerts_fired": N}

# Check alerts appeared in your app
# Open https://your-app.vercel.app/alerts
```

---

## What happens automatically after this

Every 30 min: News Agent + Technical Agent wake up, analyze all your holdings,
write alerts to Neon, push to your phone/browser, then scale back to zero.

Every 6 hours: Fundamentals + YouTube agents run.

Every morning at 7am IST: Macro agent analyzes macro environment vs your portfolio.

Your laptop can be off. Everything runs in the cloud.

---

## Adding push notifications (optional — 30 min extra)

### Android (FCM):
1. Create Firebase project at https://console.firebase.google.com
2. Download `google-services.json` → store as GCP secret `firebase-service-account`
3. Set `FIREBASE_PROJECT_ID` env var in agent Cloud Run services

### Web push (browser):
1. Generate VAPID keys: `npx web-push generate-vapid-keys`
2. Add `VAPID_PRIVATE_KEY` to GCP Secret Manager
3. Add service worker to frontend (see `frontend/public/sw.js`)

---

## Cost check after 1 month

| Item         | Expected usage         | Cost    |
|--------------|------------------------|---------|
| Claude Haiku | ~50k tokens/day        | ~$8/mo  |
| Claude Sonnet| ~10k tokens/day        | ~$12/mo |
| Neon         | < 0.1 GB               | $0      |
| Cloud Run    | ~260k vCPU-sec/month   | $0      |
| Scheduler    | 5 jobs                 | $0      |
| Vercel       | Static site            | $0      |
| Render       | 1 web service          | $0      |
| **Total**    |                        | **~$20/mo** |

---

## Troubleshooting

**Agent not firing alerts:**
- Check Cloud Run logs: `gcloud run services logs read portfolio-ai-news-agent`
- Verify holdings exist in DB
- Test agent health: `curl https://AGENT-URL/health`

**API gateway sleeping (Render free tier):**
- Expected — 10s cold start on first request after 15 min idle
- Upgrade to Render paid ($7/mo) to keep it alive if this bothers you

**LLM costs higher than expected:**
- Check `agent_runs` table: `SELECT agent_type, tokens_used FROM agent_runs ORDER BY started_at DESC LIMIT 20`
- Reduce polling frequency in Cloud Scheduler
- Switch more analysis to Haiku: set `use_sonnet=False` in agent `get_provider()` calls
