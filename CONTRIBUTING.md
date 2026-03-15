# Contributing to Portfolio AI

Welcome! This guide will get you up and running in **under 10 minutes**.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- [Node.js 18+](https://nodejs.org/) (for the frontend)
- A free [Gemini API key](https://aistudio.google.com/apikey) (the only required API key)

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/<your-org>/portfolio-ai.git
cd portfolio-ai

# Create your env file from the template
cp .env.example .env
```

### 2. Fill in your `.env`

**Required** (minimum to run):
| Variable | Where to get it | Cost |
|---|---|---|
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) | Free |
| `JWT_SECRET` | Run: `openssl rand -hex 64` | — |
| `SCHEDULER_SECRET` | Run: `openssl rand -hex 32` | — |

**Optional** (for specific agents):
| Variable | Where to get it | Cost |
|---|---|---|
| `NEWS_API_KEY` | [newsapi.org](https://newsapi.org) | Free (100 req/day) |
| `ALPHA_VANTAGE_KEY` | [alphavantage.co](https://www.alphavantage.co/support/#api-key) | Free (25 req/day) |
| `YOUTUBE_API_KEY` | [Google Cloud Console](https://console.cloud.google.com) → YouTube Data API v3 | Free |
| `FRED_API_KEY` | [FRED](https://fred.stlouisfed.org/docs/api/api_key.html) | Free |
| `ANTHROPIC_API_KEY` | [Anthropic Console](https://console.anthropic.com) | Paid (optional) |

### 3. Start everything

```bash
# Start all backend services (Postgres + 6 agents + API gateway)
cd infra/docker
docker-compose up --build -d

# The API gateway will be at http://localhost:8080
# The advisor agent will be at http://localhost:8001
```

### 4. Start the frontend

```bash
cd frontend
npm install
npm run dev

# Open http://localhost:5173
```

### 5. Create your account

Open the app in your browser and click "Create an account". That's it!

## Architecture Overview

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────────┐
│   Frontend   │────▶│  API Gateway │────▶│   Advisor Agent     │
│  React+Vite  │     │   Go + JWT   │     │  FastAPI + LLM      │
│  :5173       │     │   :8080      │     │  :8001              │
└─────────────┘     └──────┬───────┘     └─────────────────────┘
                           │
                    ┌──────▼───────┐     ┌─────────────────────┐
                    │  PostgreSQL  │◀────│  Polling Agents x5  │
                    │  :5432       │     │  news/fundamentals/ │
                    └──────────────┘     │  technical/macro/   │
                                        │  youtube            │
                                        └─────────────────────┘
```

- **Frontend** → React + TypeScript + Vite. Talks to the API gateway only.
- **API Gateway** → Go. Handles auth (JWT), rate limiting, CORS, and routes to agents.
- **Agents** → Python + FastAPI. Each agent monitors a specific data source and writes alerts to the DB.
- **Advisor** → The chat agent. Has access to your portfolio, alerts, and LLM for conversational analysis.
- **Database** → PostgreSQL. Schema is in `scripts/schema.sql`.

## Working on Specific Parts

### Frontend only
```bash
cd frontend && npm run dev
# Point to a running API (local or remote):
# Create frontend/.env.local with:
# VITE_API_URL=http://localhost:8080
```

### A single agent
```bash
# Run just postgres + the agent you're working on:
cd infra/docker
docker-compose up postgres agent-advisor -d
```

### API gateway only
```bash
cd api
go run main.go
# Requires DATABASE_URL, JWT_SECRET in env
```

## Project Structure

```
portfolio-ai/
├── agents/                 # 6 AI agents (Python + FastAPI)
│   ├── news/              # Monitors financial news
│   ├── fundamentals/      # Tracks company fundamentals
│   ├── technical/         # Technical analysis (RSI, MACD, etc.)
│   ├── macro/             # Macroeconomic indicators
│   ├── youtube/           # Financial YouTube channels
│   └── advisor/           # Chat advisor (on-demand)
├── api/                   # Go API gateway
├── shared/                # Shared Python modules
│   ├── llm/client.py     # LLM provider abstraction
│   └── db/connection.py  # DB pool + common queries
├── frontend/              # React + TypeScript + Vite
├── infra/
│   └── docker/           # docker-compose for local dev
└── scripts/
    └── schema.sql        # Database schema
```

## Key Conventions

1. **Never import LLM SDKs directly** — always use `shared/llm/client.py`'s `get_provider()`
2. **Never store secrets in code** — use `.env` locally, GCP Secret Manager in production
3. **All DB queries are parameterized** — no string concatenation for SQL
4. **Agents expose `POST /run` and `GET /health`** — called by Cloud Scheduler in prod
5. **The API gateway injects `X-User-ID`** — agents never trust client-sent user IDs

## Adding a New LLM Provider

1. Subclass `LLMProvider` in `shared/llm/client.py`
2. Implement `complete()` and `complete_chat()`
3. Add a builder function and register it in `_PROVIDER_REGISTRY`
4. Set `LLM_PROVIDER=yourprovider` in `.env`

## Testing

```bash
# Health check all services
curl http://localhost:8080/health

# Register a user
curl -X POST http://localhost:8080/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email": "test@example.com", "password": "testpassword123"}'

# Test the advisor
curl -X POST http://localhost:8080/chat \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <your-jwt-token>' \
  -d '{"message": "How is my portfolio doing?"}'
```

## Common Issues

**Docker build fails?**
- Make sure Docker has at least 4GB RAM allocated
- Run `docker-compose down -v && docker-compose up --build` for a clean start

**Database connection refused?**
- Wait for the Postgres healthcheck to pass: `docker-compose ps`
- The schema auto-applies from `scripts/schema.sql`

**LLM API errors?**
- Check your `GEMINI_API_KEY` is valid
- Gemini free tier: 1500 requests/day, 30 requests/minute
- Set `LOG_LEVEL=DEBUG` in `.env` for detailed logs

**Frontend can't connect to API?**
- Make sure `VITE_API_URL` points to your API gateway (default: `http://localhost:8080`)
- Check CORS: the API allows `localhost` origins by default

## Production Deployment

### Platforms & Services

| Service | What it runs | Free tier |
|---|---|---|
| [Vercel](https://vercel.com) | React frontend | Yes |
| [GCP Cloud Run](https://cloud.google.com/run) | 6 Python agents + Go gateway (asia-south1) | 2M req/month |
| [Neon](https://neon.tech) | PostgreSQL database | 0.5 GB free |
| [Kite Connect](https://kite.zerodha.com) | Zerodha holdings sync | ₹2000/yr |

### What auto-deploys vs manual

| Component | Service | Auto-deploy? |
|---|---|---|
| Frontend (React) | Vercel | **Yes** — on every `git push` to main |
| API Gateway (Go) | GCP Cloud Run | No — manual `gcloud run deploy` |
| 6 Agents (Python) | GCP Cloud Run | No — manual `bash scripts/deploy_agents.sh` |
| DB schema | Neon | No — manual `psql` |

### Deployment commands

```bash
# Frontend — just push to main
git push  # Vercel picks it up → deployed in ~2 min

# Gateway only
gcloud run deploy portfolio-ai-gateway \
  --source api/ --region asia-south1 --quiet

# All agents at once
bash scripts/deploy_agents.sh

# One specific agent (example: advisor)
gcloud builds submit . --config /tmp/cloudbuild-advisor.yaml --quiet
gcloud run deploy portfolio-ai-advisor-agent \
  --image gcr.io/$PROJECT_ID/portfolio-ai-advisor-agent:latest \
  --region asia-south1 --quiet
```

### Typical workflow

- **Frontend change** → `git push` → done (Vercel auto-deploys)
- **Backend change** → `git push` + run the relevant `gcloud` command
