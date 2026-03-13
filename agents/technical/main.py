"""
agents/technical/main.py — Technical Analysis Agent
Runs every 30 minutes during market hours.
Fetches OHLCV data via yfinance, computes key indicators (RSI, MACD, Bollinger Bands,
moving averages), and alerts on significant technical signals.
"""
import os, sys, json, logging
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, "/app/shared")
from llm.client import get_provider
from db.connection import (
    get_user_holdings, write_alert,
    start_agent_run, finish_agent_run, audit
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
app = FastAPI(title="Technical Agent")
SCHEDULER_SECRET = os.getenv("SCHEDULER_SECRET", "")
VALID_SEVERITIES = {"critical", "warning", "info", "opportunity"}

SYSTEM_PROMPT = """You are a technical analyst specialising in equity markets.
Analyze price action, momentum, and technical indicators for stocks in a portfolio.
Focus on high-probability setups — only alert on clear, confirmed signals.
Respond in valid JSON only."""


def build_technical_prompt(indicators_data: list[dict]) -> str:
    return f"""Analyze these technical indicators and identify significant signals.

Holdings with technical indicators:
{json.dumps(indicators_data, indent=2)}

For each stock with a significant technical signal, respond with:
{{
  "analyses": [
    {{
      "ticker": "<TICKER>",
      "signal": "bullish_breakout" | "bearish_breakdown" | "overbought" | "oversold" |
                "death_cross" | "golden_cross" | "support_test" | "resistance_test" | "neutral",
      "confidence": "high" | "medium" | "low",
      "should_alert": <true | false>,
      "alert_severity": "critical" | "warning" | "info" | "opportunity" | null,
      "alert_title": "<concise title>" | null,
      "alert_body": "<2-3 sentences describing the setup and implication>" | null,
      "key_indicators": {{"rsi": <value>, "macd_signal": "bullish|bearish", "price_vs_sma50": "<above|below>"}}
    }}
  ]
}}

Only flag stocks with clear, actionable technical signals."""


def compute_indicators(ticker: str, exchange: str) -> Optional[dict]:
    """Fetch price data and compute RSI, MACD, Bollinger Bands, SMAs."""
    try:
        import yfinance as yf
        import pandas as pd
        import requests

        yf_ticker = ticker
        if exchange in ("NSE", "BSE"):
            suffix = ".NS" if exchange == "NSE" else ".BO"
            yf_ticker = f"{ticker}{suffix}"

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
        df = yf.download(yf_ticker, period="6mo", interval="1d", progress=False, session=session)
        if df.empty or len(df) < 50:
            logger.warning(f"Insufficient price data for {ticker}")
            return None

        close = df["Close"].squeeze()

        # SMAs
        sma20  = float(close.rolling(20).mean().iloc[-1])
        sma50  = float(close.rolling(50).mean().iloc[-1])
        sma200 = float(close.rolling(200).mean().iloc[-1]) if len(df) >= 200 else None

        # RSI (14-period)
        delta  = close.diff()
        gain   = delta.clip(lower=0).rolling(14).mean()
        loss   = (-delta.clip(upper=0)).rolling(14).mean()
        # Guard: loss=0 means all gains → RSI should be 100 (overbought)
        rs     = gain / loss.replace(0, float('nan'))
        rsi    = float((100 - (100 / (1 + rs))).fillna(100).iloc[-1])

        # MACD (12, 26, 9)
        ema12  = close.ewm(span=12).mean()
        ema26  = close.ewm(span=26).mean()
        macd   = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        macd_val   = float(macd.iloc[-1])
        signal_val = float(signal.iloc[-1])

        # Bollinger Bands (20, 2σ)
        bb_mid  = close.rolling(20).mean()
        bb_std  = close.rolling(20).std()
        bb_upper = float((bb_mid + 2 * bb_std).iloc[-1])
        bb_lower = float((bb_mid - 2 * bb_std).iloc[-1])

        current_price = float(close.iloc[-1])
        prev_price    = float(close.iloc[-2])

        return {
            "ticker":        ticker,
            "exchange":      exchange,
            "current_price": round(current_price, 2),
            "change_pct":    round((current_price - prev_price) / prev_price * 100, 2),
            "sma20":         round(sma20, 2),
            "sma50":         round(sma50, 2),
            "sma200":        round(sma200, 2) if sma200 else None,
            "rsi":           round(rsi, 1),
            "macd":          round(macd_val, 4),
            "macd_signal":   round(signal_val, 4),
            "macd_histogram": round(macd_val - signal_val, 4),
            "bb_upper":      round(bb_upper, 2),
            "bb_lower":      round(bb_lower, 2),
            "price_vs_sma50":  "above" if current_price > sma50 else "below",
            "price_vs_sma200": "above" if sma200 and current_price > sma200 else "below",
        }

    except Exception as e:
        logger.warning(f"Technical indicator computation failed for {ticker}: {e}")
        return None


def run_technical_agent(user_id: str) -> dict:
    run_id = start_agent_run("technical")
    audit("agent_run_started", user_id=user_id, resource_type="agent_run", resource_id=run_id)

    holdings = get_user_holdings(user_id)
    if not holdings:
        finish_agent_run(run_id, "success", alerts_fired=0)
        return {"status": "success", "message": "No holdings configured", "alerts_fired": 0}

    indicators_data = []
    for h in holdings:
        data = compute_indicators(h["ticker"], h["exchange"])
        if data:
            data["avg_cost"] = float(h["avg_cost"])
            indicators_data.append(data)

    if not indicators_data:
        finish_agent_run(run_id, "success", alerts_fired=0)
        return {"status": "success", "message": "No market data available (rate limited or market closed)", "alerts_fired": 0}

    alerts_fired = 0
    try:
        provider = get_provider(os.getenv("LLM_PROVIDER", "gemini"), use_sonnet=False)
        response = provider.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_technical_prompt(indicators_data),
            max_tokens=2048,
            temperature=0.1,
        )
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
        for analysis in result.get("analyses", []):
            if not analysis.get("should_alert"):
                continue
            severity = analysis.get("alert_severity")
            title    = analysis.get("alert_title")
            body     = analysis.get("alert_body")
            ticker   = analysis.get("ticker")
            if not severity or severity not in VALID_SEVERITIES:
                logger.warning(f"Technical agent skipping alert — invalid severity: {severity!r}")
                continue
            if not title or not body or not ticker:
                logger.warning("Technical agent skipping alert — missing required fields")
                continue
            write_alert(
                user_id=user_id,
                agent_type="technical",
                severity=severity,
                title=title,
                body=body,
                ticker=ticker,
                llm_provider=response.provider,
                raw_llm_output=result,
                data_sources={"indicators": analysis.get("key_indicators", {})},
            )
            alerts_fired += 1
    except json.JSONDecodeError as e:
        logger.error(f"Technical agent JSON parse failed: {e}")
        finish_agent_run(run_id, "failed", error_message=str(e))
        return {"status": "failed", "error": str(e)}
    except Exception as e:
        logger.error(f"Technical agent run failed: {e}")
        finish_agent_run(run_id, "failed", error_message=str(e))
        return {"status": "failed", "error": str(e)}

    tokens = response.input_tokens + response.output_tokens
    finish_agent_run(run_id, "success", alerts_fired=alerts_fired, tokens_used=tokens)
    audit("agent_run_completed", user_id=user_id, resource_type="agent_run", resource_id=run_id)
    return {
        "status": "success",
        "holdings_analysed": len(indicators_data),
        "alerts_fired": alerts_fired,
        "tokens_used": tokens,
    }


class RunRequest(BaseModel):
    user_id: str

@app.post("/run")
async def trigger_run(request: RunRequest, x_scheduler_secret: Optional[str] = Header(None)):
    if SCHEDULER_SECRET and x_scheduler_secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return run_technical_agent(request.user_id)

@app.get("/health")
async def health():
    return {"status": "ok", "agent": "technical"}
