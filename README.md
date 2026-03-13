# Portfolio AI

A self-hosted, AI-powered portfolio monitoring and advisory system.
6 specialised agents run continuously — analysing news, fundamentals, technicals, macro data, and YouTube — and surface alerts to a chat-based advisor.

**Currently built for Indian retail investors (NSE/BSE via Zerodha Kite), with US market support planned.**

> This project is in active development. Run your own instance, contribute, and help shape it into something genuinely useful.

---

## What it does

| Agent | What it monitors | How often |
|---|---|---|
| **News** | Headlines from NewsAPI + RSS feeds → sentiment per holding | Every 30 min |
| **Fundamentals** | P/E, P/B, ROE, debt ratios via yfinance | Every 6h |
| **Technical** | RSI, MACD, Bollinger Bands, moving averages | Every 30 min |
| **Macro** | FRED data (interest rates, CPI, GDP, yield curve) + India RSS | Daily 7am IST |
| **YouTube** | Transcripts from finance channels → insights per holding | Every 6h |
| **Advisor** | Chat interface with full portfolio context | On-demand |

All agents use **Gemini Flash** (free tier) by default. Switch to Claude for higher quality analysis.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  React + TypeScript frontend  (Vercel)                      │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTPS
┌────────────────────────▼────────────────────────────────────┐
│  Go API Gateway  (Render.com free tier)                     │
│  JWT auth · rate limiting · CORS · Zerodha sync             │
└────┬───────────────────────────────────────────────┬────────┘
     │ SQL                                           │ HTTP
┌────▼──────────┐              ┌─────────────────────▼───────┐
│  Neon         │              │  Python agents  (Cloud Run) │
│  PostgreSQL   │◄─────────────│  FastAPI · shared LLM layer │
└───────────────┘              └─────────────────────────────┘
```

**Stack:**
- Agents: Python 3.12 + FastAPI → Google Cloud Run (free tier)
- API gateway: Go → Render.com (free tier)
- Frontend: React + TypeScript + Vite → Vercel (free tier)
- Database: Neon PostgreSQL (free tier)
- Scheduling: GCP Cloud Scheduler
- LLM: Gemini Flash (free) / Claude Haiku+Sonnet (paid)

**Running cost: ~$0/month** on free tiers for personal use.

---

## Running locally

### Prerequisites

- Docker + Docker Compose
- Node.js 20+
- Go 1.22+ (only if editing the API gateway)
- API keys (see below)

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/portfolio-ai.git
cd portfolio-ai

cp .env.example .env
# Edit .env — minimum required: GEMINI_API_KEY, JWT_SECRET, SCHEDULER_SECRET
```

**Minimum keys to get started:**

| Key | Where to get | Cost |
|---|---|---|
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com/app/apikey) | Free |
| `JWT_SECRET` | `openssl rand -hex 64` | — |
| `SCHEDULER_SECRET` | `openssl rand -hex 32` | — |
| `FRED_API_KEY` | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) | Free |

### 2. Start all services

```bash
cd infra/docker
ln -s ../../.env .env        # symlink so docker-compose picks up your keys
docker-compose up -d
```

Services:
- API gateway: http://localhost:8080
- Advisor agent: http://localhost:8001
- PostgreSQL: localhost:5432

### 3. Create your account

```bash
curl -X POST http://localhost:8080/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"yourpassword","name":"Your Name"}'
```

### 4. Start the frontend

```bash
cd frontend
npm install
npm run dev
# Open http://localhost:3000
```

### 5. Add holdings manually (or sync from Zerodha)

**Manual:**
```bash
# Get your user_id from the register response, then:
docker exec portfolio-ai_postgres_1 psql -U dev_user -d portfolio_ai -c "
INSERT INTO holdings (user_id, ticker, exchange, company_name, sector, quantity, avg_cost, currency)
VALUES ('YOUR_USER_ID', 'RELIANCE', 'NSE', 'Reliance Industries', 'Energy', 10, 2450.00, 'INR');
"
```

**Via Zerodha Kite (automated):**
1. Register a Kite Connect app at [kite.zerodha.com/developers](https://kite.zerodha.com/developers)
2. Set redirect URL to `http://localhost:8080/admin/zerodha/callback`
3. Add your `ZERODHA_API_KEY` and `ZERODHA_API_SECRET` to `.env`
4. Daily login flow:
   ```
   # Open in browser:
   https://kite.zerodha.com/connect/login?api_key=YOUR_KEY&v=3

   # After login, you'll be redirected with a request_token. Then:
   curl -X POST http://localhost:8080/admin/zerodha/sync \
     -H "Authorization: Bearer YOUR_JWT" \
     -H "Content-Type: application/json" \
     -d '{"request_token": "TOKEN_FROM_REDIRECT_URL"}'
   ```

### 6. Trigger agents manually (optional)

```bash
SECRET="your_scheduler_secret"
USER_ID="your_user_id"

# Run news agent
curl -X POST http://localhost:8001/run \  # wrong port — exec into container
# Easier:
docker exec portfolio-ai_agent-news_1 python3 -c "
import urllib.request, json
req = urllib.request.Request('http://localhost:8080/run',
  data=json.dumps({'user_id':'$USER_ID'}).encode(),
  headers={'Content-Type':'application/json','X-Scheduler-Secret':'$SECRET'},
  method='POST')
with urllib.request.urlopen(req, timeout=90) as r: print(r.read().decode())
"
```

---

## Deploying to production (free tier)

### Database — Neon

1. Create a free project at [neon.tech](https://neon.tech)
2. Copy the connection string → `DATABASE_URL` in your secrets
3. Run the schema: `psql $DATABASE_URL < scripts/schema.sql`

### Secrets — GCP Secret Manager

```bash
# One-time setup
bash scripts/setup_gcp.sh

# Set SECRETS_SOURCE=gcp in Cloud Run env vars
```

### Agents — Google Cloud Run

```bash
# Deploy all agents (builds images, pushes to Artifact Registry, deploys)
bash scripts/deploy_agents.sh
```

Each agent becomes a Cloud Run service. Cloud Scheduler triggers them on the polling intervals above.

### API Gateway — Render.com

1. Connect your GitHub repo at [render.com](https://render.com)
2. New Web Service → root directory: `api/` → runtime: Go
3. Set environment variables from `.env.example`
4. Auto-deploys on every push to `main`

### Frontend — Vercel

```bash
# Set VITE_API_URL to your Render URL
echo "VITE_API_URL=https://your-api.onrender.com" > frontend/.env.production
```

1. Import repo at [vercel.com](https://vercel.com)
2. Framework: Vite → root: `frontend/`
3. Add `VITE_API_URL` environment variable
4. Auto-deploys on every push to `main`

---

## Contributing

This project is designed to be forked and self-hosted. Contributions that improve the shared codebase are welcome.

**Good areas to contribute:**
- US market support (Interactive Brokers / Alpaca integration instead of Zerodha)
- Better LLM prompts for more accurate analysis
- Additional data sources (earnings calendars, insider trading, options flow)
- Push notifications (FCM / APNs / Web Push — stub is in `shared/notifications/`)
- Multi-currency portfolio support
- Tests

**How to contribute:**
1. Fork the repo
2. Create a branch: `git checkout -b feature/your-feature`
3. Run locally to verify it works
4. Open a PR with a clear description of what changed and why

**Note:** The project is intentionally simple and free-tier first. Keep dependencies minimal and avoid adding paid services.

---

## Project structure

```
portfolio-ai/
├── agents/
│   ├── news/          # FastAPI agent — NewsAPI + RSS → Gemini sentiment
│   ├── fundamentals/  # FastAPI agent — yfinance → Gemini valuation
│   ├── technical/     # FastAPI agent — yfinance indicators → Gemini signals
│   ├── macro/         # FastAPI agent — FRED API → Gemini macro analysis
│   ├── youtube/       # FastAPI agent — YouTube transcripts → insights
│   └── advisor/       # FastAPI agent — chat with portfolio context
├── api/               # Go API gateway — auth, holdings, alerts, chat proxy
├── shared/
│   ├── llm/client.py  # LLM abstraction — Gemini + Claude providers
│   └── db/connection.py # PostgreSQL connection pool + shared queries
├── frontend/          # React + TypeScript + Vite
├── scripts/
│   ├── schema.sql     # Full database schema
│   ├── deploy_agents.sh
│   └── setup_gcp.sh
└── infra/
    ├── docker/        # Local dev docker-compose
    └── cloud-run/     # GCP Cloud Run YAML configs
```

---

## Roadmap

- [ ] Zerodha Kite OAuth callback page in the frontend (currently manual)
- [ ] Push notifications when critical alerts fire
- [ ] US market support (Interactive Brokers / Alpaca)
- [ ] Multi-user instances with invite system
- [ ] Mobile app (React Native, reusing the same API)
- [ ] GPT-4o consensus scoring (Phase 2 — stubbed in `shared/llm/client.py`)
- [ ] Backtesting alerts against historical price data

---

## License

MIT — use it, fork it, build on it.
