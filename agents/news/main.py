"""
agents/news/main.py — News Sentiment Agent
Runs every 30 minutes.
Fetches headlines from NewsAPI + RSS feeds, scores sentiment per holding,
and fires alerts on significant bearish or bullish signals.
"""
import os, sys, json, logging
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
import httpx
import feedparser

sys.path.insert(0, "/app/shared")
from llm.client import get_provider
from db.connection import (
    get_user_holdings, get_all_tickers,
    write_alert, start_agent_run, finish_agent_run, audit
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
app = FastAPI(title="News Agent")
SCHEDULER_SECRET = os.getenv("SCHEDULER_SECRET", "")
VALID_SEVERITIES = {"critical", "warning", "info", "opportunity"}

NEWS_API_BASE = "https://newsapi.org/v2"

# RSS feeds for Indian market news
INDIA_RSS_FEEDS = [
    "https://economictimes.indiatimes.com/markets/stocks/rss.cms",
    "https://www.moneycontrol.com/rss/marketreports.xml",
    "https://feeds.feedburner.com/ndtvprofit-latest",
]

SYSTEM_PROMPT = """You are a financial news analyst.
Analyze news headlines and their sentiment impact on specific stocks in a portfolio.
Be precise — only flag genuinely significant news, not routine updates.
Respond in valid JSON only."""


def _sanitize(text: str) -> str:
    """Remove characters that can break JSON strings in LLM output."""
    return text.replace('"', "'").replace("\\", "").replace("\n", " ").replace("\r", "").strip()

def build_news_prompt(tickers: list[str], headlines: list[str]) -> str:
    clean_headlines = [_sanitize(h) for h in headlines[:30] if h.strip()]
    return f"""Analyze these news headlines for sentiment impact on portfolio stocks.

Portfolio tickers: {', '.join(tickers)}

Recent headlines:
{chr(10).join(f'- {h}' for h in clean_headlines)}

For each ticker that has significant news, respond with valid JSON only:
{{
  "analyses": [
    {{
      "ticker": "<TICKER>",
      "sentiment": "bullish or bearish or neutral",
      "confidence": "high or medium or low",
      "should_alert": true,
      "alert_severity": "critical or warning or info or opportunity",
      "alert_title": "<concise title under 100 chars>",
      "alert_body": "<2-3 sentence summary>",
      "relevant_headlines": ["<headline>"]
    }}
  ]
}}

Only include tickers with high-confidence, actionable signals. Skip neutral/routine news.
IMPORTANT: Return only valid JSON. Do not include any text before or after the JSON."""


def fetch_newsapi_headlines(tickers: list[str], api_key: str) -> list[str]:
    """Fetch headlines from NewsAPI for the given tickers. Returns up to 20 headlines."""
    if not api_key:
        return []
    query = " OR ".join(tickers[:5])  # NewsAPI free tier: keep query short
    try:
        resp = httpx.get(
            f"{NEWS_API_BASE}/everything",
            params={
                "q": query,
                "apiKey": api_key,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 20,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        articles = resp.json().get("articles", [])
        return [
            f"{a['title']} — {a.get('source', {}).get('name', '')}"
            for a in articles
            if a.get("title")
        ]
    except Exception as e:
        logger.warning(f"NewsAPI fetch failed: {e}")
        return []


def fetch_rss_headlines() -> list[str]:
    """Fetch headlines from Indian market RSS feeds."""
    headlines = []
    for url in INDIA_RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:8]:
                title = entry.get("title", "").strip()
                if title:
                    headlines.append(title)
        except Exception as e:
            logger.warning(f"RSS feed failed {url}: {e}")
    return headlines


def run_news_agent(user_id: str) -> dict:
    run_id = start_agent_run("news")
    audit("agent_run_started", user_id=user_id, resource_type="agent_run", resource_id=run_id)

    tickers = get_all_tickers(user_id)
    if not tickers:
        finish_agent_run(run_id, "success", alerts_fired=0)
        return {"status": "success", "message": "No holdings configured", "alerts_fired": 0}

    news_api_key = os.getenv("NEWS_API_KEY", "")
    newsapi_headlines = fetch_newsapi_headlines(tickers, news_api_key)
    rss_headlines    = fetch_rss_headlines()
    all_headlines    = newsapi_headlines + rss_headlines

    if not all_headlines:
        finish_agent_run(run_id, "success", alerts_fired=0)
        return {"status": "success", "message": "No headlines fetched", "alerts_fired": 0}

    alerts_fired = 0
    try:
        provider = get_provider(os.getenv("LLM_PROVIDER", "gemini"), use_sonnet=False)
        response = provider.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_news_prompt(tickers, all_headlines),
            max_tokens=2048,
            temperature=0.1,
        )
        # Strip markdown code fences Gemini sometimes wraps JSON in
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0].strip()
        logger.debug(f"LLM raw response: {raw[:300]}")
        # Try to extract a valid JSON object even if the output is partially truncated
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Fall back: extract just the analyses array if the outer object is broken
            import re as _re
            m = _re.search(r'"analyses"\s*:\s*(\[.*?\])', raw, _re.DOTALL)
            if m:
                try:
                    analyses = json.loads(m.group(1))
                    result = {"analyses": analyses}
                except json.JSONDecodeError:
                    raise
            else:
                raise
        for analysis in result.get("analyses", []):
            if not analysis.get("should_alert"):
                continue
            severity = analysis.get("alert_severity")
            title    = analysis.get("alert_title")
            body     = analysis.get("alert_body")
            ticker   = analysis.get("ticker")
            if not severity or severity not in VALID_SEVERITIES:
                logger.warning(f"News agent skipping alert — invalid severity: {severity!r}")
                continue
            if not title or not body or not ticker:
                logger.warning("News agent skipping alert — missing required fields")
                continue
            write_alert(
                user_id=user_id,
                agent_type="news",
                severity=severity,
                title=title,
                body=body,
                ticker=ticker,
                llm_provider=response.provider,
                raw_llm_output=result,
                data_sources={
                    "headlines_count": len(all_headlines),
                    "relevant_headlines": analysis.get("relevant_headlines", []),
                },
            )
            alerts_fired += 1
    except json.JSONDecodeError as e:
        logger.error(f"News agent JSON parse failed: {e}")
        finish_agent_run(run_id, "failed", error_message=str(e))
        return {"status": "failed", "error": str(e)}
    except Exception as e:
        logger.error(f"News agent run failed: {e}")
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
    return run_news_agent(request.user_id)

@app.get("/health")
async def health():
    return {"status": "ok", "agent": "news"}
