"""
agents/macro/main.py — Macro / Business Cycle Agent
Runs once daily at 7am IST (before market open).
Monitors: interest rates, inflation, GDP, yield curve, sector rotation signals.
Uses FRED API (free, no rate limits for basic data).
"""
import os, sys, json, logging
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
import httpx

sys.path.insert(0, "/app/shared")
from llm.client import get_provider
from db.connection import (
    get_user_holdings, write_alert,
    start_agent_run, finish_agent_run, audit
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
app = FastAPI(title="Macro Agent")
SCHEDULER_SECRET = os.getenv("SCHEDULER_SECRET", "")

# FRED series IDs for key macro indicators
FRED_SERIES = {
    "fed_funds_rate":     "FEDFUNDS",
    "cpi_yoy":            "CPIAUCSL",
    "us_gdp_growth":      "A191RL1Q225SBEA",
    "unemployment":       "UNRATE",
    "yield_10y":          "DGS10",
    "yield_2y":           "DGS2",
    "yield_spread_10_2":  "T10Y2Y",      # Yield curve spread (recession indicator)
    "vix":                "VIXCLS",
}

INDIA_RSS_FEEDS = [
    "https://www.rbi.org.in/rss/rss.aspx",           # RBI announcements
    "https://economictimes.indiatimes.com/economy/rss.cms",
]

SYSTEM_PROMPT = """You are a macro economist and portfolio strategist.
You analyze macroeconomic indicators and their impact on a specific equity portfolio.
Focus on actionable implications — how should the portfolio be positioned given current macro?
Respond in valid JSON only."""

def fetch_fred_data() -> dict:
    """Fetch latest values for key FRED series. FRED API is free with registration."""
    fred_key = os.getenv("FRED_API_KEY", "")
    results = {}

    if not fred_key:
        logger.warning("FRED_API_KEY not set — skipping FRED data")
        return results

    for label, series_id in FRED_SERIES.items():
        try:
            resp = httpx.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": fred_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 2,           # latest + previous for trend
                },
                timeout=10.0,
            )
            obs = resp.json().get("observations", [])
            if len(obs) >= 1:
                results[label] = {
                    "current": obs[0]["value"],
                    "previous": obs[1]["value"] if len(obs) > 1 else None,
                    "date": obs[0]["date"],
                }
        except Exception as e:
            logger.warning(f"FRED fetch failed for {series_id}: {e}")

    return results

def fetch_india_macro_news() -> list[str]:
    """Fetch RBI and macro news from Indian sources."""
    import feedparser
    headlines = []
    for feed_url in INDIA_RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                headlines.append(entry.get("title", ""))
        except Exception as e:
            logger.warning(f"India macro RSS failed: {e}")
    return headlines

def get_portfolio_sector_summary(user_id: str) -> dict:
    """Summarise portfolio sector exposure for macro context."""
    holdings = get_user_holdings(user_id)
    sectors = {}
    total = 0
    for h in holdings:
        price = h.get("last_price") or h["avg_cost"]
        val = float(price) * float(h["quantity"])
        sector = h.get("sector", "Unknown")
        sectors[sector] = sectors.get(sector, 0) + val
        total += val
    if total == 0:
        return {}
    return {s: round(v / total * 100, 1) for s, v in sectors.items()}

def build_macro_prompt(macro_data: dict, india_headlines: list, sector_exposure: dict) -> str:
    return f"""Analyze the current macro environment and its impact on this portfolio.

GLOBAL MACRO INDICATORS:
{json.dumps(macro_data, indent=2)}

INDIA MACRO HEADLINES:
{chr(10).join(f'- {h}' for h in india_headlines[:8])}

PORTFOLIO SECTOR EXPOSURE:
{json.dumps(sector_exposure, indent=2)}

Respond with:
{{
  "macro_regime": "risk_on" | "risk_off" | "stagflation" | "recovery" | "neutral",
  "yield_curve_signal": "normal" | "inverted" | "flattening" | "steepening",
  "portfolio_risk_score": <integer 1-10, 10=highest risk given current macro>,
  "should_alert": <true | false>,
  "alert_severity": "critical" | "warning" | "info" | "opportunity" | null,
  "alert_title": "<title>" | null,
  "alert_body": "<2-3 sentences specific to THIS portfolio's macro exposure>" | null,
  "overweight_sectors": ["<sectors to overweight in this macro>"],
  "underweight_sectors": ["<sectors to underweight in this macro>"],
  "key_risks": ["<risk1>", "<risk2>"],
  "key_opportunities": ["<opportunity1>"]
}}"""

def run_macro_agent(user_id: str) -> dict:
    run_id = start_agent_run("macro")
    audit("agent_run_started", user_id=user_id, resource_type="agent_run", resource_id=run_id)

    macro_data      = fetch_fred_data()
    india_headlines = fetch_india_macro_news()
    sector_exposure = get_portfolio_sector_summary(user_id)

    if not macro_data and not india_headlines:
        finish_agent_run(run_id, "success", alerts_fired=0)
        return {"status": "success", "message": "No macro data available", "alerts_fired": 0}

    provider = get_provider(os.getenv("LLM_PROVIDER", "gemini"), use_sonnet=False)
    response = provider.complete(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=build_macro_prompt(macro_data, india_headlines, sector_exposure),
        max_tokens=2048,
        temperature=0.2,
    )

    alerts_fired = 0
    try:
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        logger.debug(f"Macro LLM raw: {raw[:300]}")
        analysis = json.loads(raw)
        if analysis.get("should_alert"):
            write_alert(
                user_id=user_id,
                agent_type="macro",
                severity=analysis["alert_severity"],
                title=analysis["alert_title"],
                body=analysis["alert_body"],
                ticker=None,           # macro alerts are portfolio-level
                llm_provider=response.provider,
                raw_llm_output=response.raw,
                data_sources={"macro": macro_data, "headlines": india_headlines},
            )
            alerts_fired = 1
    except json.JSONDecodeError as e:
        logger.error(f"Macro agent LLM JSON parse failed: {e}")
        finish_agent_run(run_id, "failed", error_message=str(e))
        return {"status": "failed", "error": str(e)}

    tokens = response.input_tokens + response.output_tokens
    finish_agent_run(run_id, "success", alerts_fired=alerts_fired, tokens_used=tokens)
    audit("agent_run_completed", user_id=user_id, resource_type="agent_run", resource_id=run_id)
    return {"status": "success", "alerts_fired": alerts_fired, "tokens_used": tokens}


class RunRequest(BaseModel):
    user_id: str

@app.post("/run")
async def trigger_run(request: RunRequest, x_scheduler_secret: Optional[str] = Header(None)):
    if SCHEDULER_SECRET and x_scheduler_secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return run_macro_agent(request.user_id)

@app.get("/health")
async def health():
    return {"status": "ok", "agent": "macro"}
