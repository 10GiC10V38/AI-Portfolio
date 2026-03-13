# Portfolio AI — Cursor Context File
# Paste this at the start of EVERY Cursor session to maintain architectural consistency.

## What this project is
A personal portfolio monitoring + advisory application with 6 AI agents that run 24/7
in the cloud, analyzing stocks via real-time data and alerting via a dedicated mobile/web app.

## Current phase: PHASE 1
- Single LLM: Claude (claude-haiku-4-5-20251001 for polling, claude-sonnet-4-6 for deep analysis)
- All infrastructure: free tier only
- No Telegram, no AWS, no paid services except Claude API (~$20/mo)

## Agreed stack — DO NOT deviate from this
| Layer            | Technology                        |
|------------------|-----------------------------------|
| Agents           | Python 3.12 + FastAPI             |
| API gateway      | Go (Render.com free)              |
| Frontend         | React + TypeScript (Vercel)       |
| Mobile           | React Native                      |
| Database         | Neon PostgreSQL (free tier)       |
| Secrets          | GCP Secret Manager (free tier)    |
| Agent hosting    | Google Cloud Run (free tier)      |
| Scheduling       | GCP Cloud Scheduler (free tier)   |
| Push (mobile)    | FCM (Android) + APNs (iOS)        |
| Push (web)       | Web Push API                      |
| Email fallback   | Resend.com (free tier)            |
| Market data      | yfinance + Alpha Vantage free     |
| News             | NewsAPI.org + RSS feeds           |
| YouTube          | YouTube Data API v3 (free)        |
| LLM              | Anthropic Claude API              |

## Project structure
portfolio-ai/
├── agents/
│   ├── news/           # News sentiment agent
│   ├── fundamentals/   # Fundamentals agent
│   ├── technical/      # Technical analysis agent
│   ├── macro/          # Macro/cycle agent
│   ├── youtube/        # YouTube transcript agent
│   └── advisor/        # Portfolio advisor (chat) agent
├── api/                # Go API gateway
│   ├── routes/
│   ├── middleware/
│   └── models/
├── shared/
│   ├── llm/            # LLM client abstraction (Phase 2-ready)
│   ├── db/             # DB connection + queries
│   └── notifications/  # FCM + APNs + Web Push
├── frontend/           # React + TypeScript web app
├── infra/
│   ├── docker/         # docker-compose for local dev
│   ├── cloud-run/      # Cloud Run service YAMLs
│   └── scheduler/      # Cloud Scheduler job configs
└── scripts/            # DB migrations, setup scripts

## Security rules — enforce these always
1. NEVER store secrets in .env files committed to git
2. ALL secrets come from GCP Secret Manager via the secrets loader
3. Brokerage API keys are READ-ONLY scoped — never request write permissions
4. All DB queries use parameterized statements — no string concatenation
5. All agent output is sanitized before writing to DB or sending as alert
6. Every agent action is logged to the audit_log table
7. JWT tokens expire in 1 hour — refresh token pattern enforced

## Phase 2 readiness rules
- llm_client.py MUST use the provider abstraction — never call anthropic directly
- Every agent MUST accept a `llm_provider` parameter defaulting to "claude"
- Consensus orchestrator interface must be stubbed even if unused in Phase 1

## Agents — polling intervals (Cloud Scheduler)
- News agent:         every 30 minutes
- Fundamentals agent: every 6 hours
- Technical agent:    every 30 minutes
- Macro agent:        every 24 hours
- YouTube agent:      every 6 hours
- Advisor agent:      on-demand only (triggered by user chat)

## Zerodha integration — manual login (intentional, no TOTP stored)
Daily workflow (30 seconds):
1. Open: https://kite.trade/connect/login?api_key=YOUR_KEY&v=3
2. Log in on Zerodha's own page
3. Copy ?request_token=xxxx from the redirect URL
4. App Admin page → paste → POST /admin/zerodha/sync → live holdings in DB

Sync agent ONLY needs: ZERODHA_API_KEY + ZERODHA_API_SECRET
NO password, NO TOTP secret stored anywhere.

## Env vars required
ANTHROPIC_API_KEY, DATABASE_URL, NEWS_API_KEY, ALPHA_VANTAGE_KEY,
YOUTUBE_API_KEY, FRED_API_KEY, JWT_SECRET, SCHEDULER_SECRET,
ZERODHA_API_KEY, ZERODHA_API_SECRET
SECRETS_SOURCE=env (local) | gcp (Cloud Run)
