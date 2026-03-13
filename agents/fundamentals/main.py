"""
agents/fundamentals/main.py — Fundamentals Agent
Runs every 6 hours.
Fetches key financial ratios and metrics via yfinance + Alpha Vantage,
analyses valuation vs. historical norms, and alerts on significant changes.
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
app = FastAPI(title="Fundamentals Agent")
SCHEDULER_SECRET = os.getenv("SCHEDULER_SECRET", "")

SYSTEM_PROMPT = """You are a fundamental equity analyst.
Analyze financial metrics for stocks in a portfolio and identify significant valuation
changes, deteriorating fundamentals, or compelling value opportunities.
Be conservative — only flag genuinely significant changes, not routine fluctuations.
Respond in valid JSON only."""


def build_fundamentals_prompt(holdings_data: list[dict]) -> str:
    return f"""Analyze these stock fundamentals and identify significant signals.

Holdings with current metrics:
{json.dumps(holdings_data, indent=2)}

For each stock with a significant fundamental signal, respond with:
{{
  "analyses": [
    {{
      "ticker": "<TICKER>",
      "signal": "overvalued" | "undervalued" | "deteriorating" | "improving" | "neutral",
      "confidence": "high" | "medium" | "low",
      "should_alert": <true | false>,
      "alert_severity": "critical" | "warning" | "info" | "opportunity" | null,
      "alert_title": "<concise title>" | null,
      "alert_body": "<2-3 sentences with specific metrics>" | null,
      "key_metrics": {{"pe": <value>, "pb": <value>, "roe": <value>}}
    }}
  ]
}}

Only flag stocks with clear, high-conviction fundamental concerns or opportunities."""


def fetch_yfinance_metrics(ticker: str, exchange: str) -> Optional[dict]:
    """Fetch fundamental metrics using yfinance."""
    try:
        import yfinance as yf
        import requests as req_lib
        # For Indian stocks, append exchange suffix
        yf_ticker = ticker
        if exchange in ("NSE", "BSE"):
            suffix = ".NS" if exchange == "NSE" else ".BO"
            yf_ticker = f"{ticker}{suffix}"

        session = req_lib.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        stock = yf.Ticker(yf_ticker, session=session)
        info  = stock.info

        return {
            "ticker":           ticker,
            "exchange":         exchange,
            "company_name":     info.get("longName", ticker),
            "sector":           info.get("sector"),
            "pe_ratio":         info.get("trailingPE"),
            "forward_pe":       info.get("forwardPE"),
            "pb_ratio":         info.get("priceToBook"),
            "roe":              info.get("returnOnEquity"),
            "debt_to_equity":   info.get("debtToEquity"),
            "revenue_growth":   info.get("revenueGrowth"),
            "earnings_growth":  info.get("earningsGrowth"),
            "profit_margins":   info.get("profitMargins"),
            "dividend_yield":   info.get("dividendYield"),
            "52w_high":         info.get("fiftyTwoWeekHigh"),
            "52w_low":          info.get("fiftyTwoWeekLow"),
            "current_price":    info.get("currentPrice"),
            "market_cap":       info.get("marketCap"),
        }
    except Exception as e:
        logger.warning(f"yfinance failed for {ticker}: {e}")
        return None


def run_fundamentals_agent(user_id: str) -> dict:
    run_id = start_agent_run("fundamentals")
    audit("agent_run_started", user_id=user_id, resource_type="agent_run", resource_id=run_id)

    holdings = get_user_holdings(user_id)
    if not holdings:
        finish_agent_run(run_id, "success", alerts_fired=0)
        return {"status": "success", "message": "No holdings configured", "alerts_fired": 0}

    holdings_data = []
    for h in holdings:
        metrics = fetch_yfinance_metrics(h["ticker"], h["exchange"])
        if metrics:
            metrics["avg_cost"]   = float(h["avg_cost"])
            metrics["quantity"]   = float(h["quantity"])
            holdings_data.append(metrics)

    if not holdings_data:
        finish_agent_run(run_id, "success", alerts_fired=0)
        return {"status": "success", "message": "No market data available (rate limited or market closed)", "alerts_fired": 0}

    provider = get_provider(os.getenv("LLM_PROVIDER", "gemini"), use_sonnet=False)
    response = provider.complete(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=build_fundamentals_prompt(holdings_data),
        max_tokens=2048,
        temperature=0.2,
    )

    alerts_fired = 0
    try:
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
        for analysis in result.get("analyses", []):
            if not analysis.get("should_alert"):
                continue
            write_alert(
                user_id=user_id,
                agent_type="fundamentals",
                severity=analysis["alert_severity"],
                title=analysis["alert_title"],
                body=analysis["alert_body"],
                ticker=analysis["ticker"],
                llm_provider=response.provider,
                raw_llm_output=result,
                data_sources={"metrics": analysis.get("key_metrics", {})},
            )
            alerts_fired += 1
    except json.JSONDecodeError as e:
        logger.error(f"Fundamentals agent JSON parse failed: {e}")
        finish_agent_run(run_id, "failed", error_message=str(e))
        return {"status": "failed", "error": str(e)}

    tokens = response.input_tokens + response.output_tokens
    finish_agent_run(run_id, "success", alerts_fired=alerts_fired, tokens_used=tokens)
    audit("agent_run_completed", user_id=user_id, resource_type="agent_run", resource_id=run_id)
    return {
        "status": "success",
        "holdings_analysed": len(holdings_data),
        "alerts_fired": alerts_fired,
        "tokens_used": tokens,
    }


class RunRequest(BaseModel):
    user_id: str

@app.post("/run")
async def trigger_run(request: RunRequest, x_scheduler_secret: Optional[str] = Header(None)):
    if SCHEDULER_SECRET and x_scheduler_secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return run_fundamentals_agent(request.user_id)

@app.get("/health")
async def health():
    return {"status": "ok", "agent": "fundamentals"}
