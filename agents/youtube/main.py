"""
agents/youtube/main.py — YouTube Intelligence Agent
Runs every 6 hours.
Fetches new videos from subscribed channels, extracts transcripts,
filters for holding relevance, and stores structured insights.
"""
import os, sys, json, logging
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
import httpx
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled

sys.path.insert(0, "/app/shared")
from llm.client import get_provider
from db.connection import (
    get_user_holdings, get_all_tickers,
    write_alert, start_agent_run, finish_agent_run,
    audit, get_conn
)
from psycopg2.extras import Json

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
app = FastAPI(title="YouTube Agent")
SCHEDULER_SECRET = os.getenv("SCHEDULER_SECRET", "")

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# ── Prompts ───────────────────────────────────────────────────────────────────

RELEVANCE_SYSTEM = """You are a financial content classifier.
Given a video title and partial transcript, determine if the video discusses
specific stocks from a provided watchlist. Respond in JSON only."""

INSIGHT_SYSTEM = """You are a financial analyst extracting structured insights
from video transcripts. Be precise — only extract views the creator clearly expressed.
Respond in JSON only."""

def build_relevance_prompt(title: str, transcript_snippet: str, tickers: list[str]) -> str:
    return f"""Video title: {title}

First 500 words of transcript:
{transcript_snippet[:2000]}

Watchlist tickers: {', '.join(tickers)}

Does this video discuss any of these tickers or their parent companies?
{{
  "is_relevant": <true | false>,
  "mentioned_tickers": ["<ticker>"],
  "confidence": "high" | "medium" | "low"
}}"""

def build_insight_prompt(title: str, transcript: str, tickers: list[str]) -> str:
    return f"""Video: "{title}"

Transcript:
{transcript[:6000]}

Extract insights for these tickers only: {', '.join(tickers)}

Respond with:
{{
  "insights": [
    {{
      "ticker": "<TICKER>",
      "stance": "bullish" | "bearish" | "neutral",
      "confidence": "high" | "medium" | "low",
      "key_points": ["<point1>", "<point2>"],
      "summary": "<1-2 sentence summary of creator's view>",
      "timestamp_hint": "<approximate video section if known>"
    }}
  ],
  "overall_market_view": "bullish" | "bearish" | "neutral" | null,
  "key_themes": ["<theme1>"]
}}"""

# ── YouTube API helpers ───────────────────────────────────────────────────────

def get_channel_recent_videos(channel_id: str, api_key: str, max_results: int = 5) -> list[dict]:
    """Fetch recent videos from a channel. Uses ~3 API quota units per call."""
    try:
        resp = httpx.get(
            f"{YOUTUBE_API_BASE}/search",
            params={
                "key": api_key,
                "channelId": channel_id,
                "part": "snippet",
                "order": "date",
                "maxResults": max_results,
                "type": "video",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return [
            {
                "video_id":    item["id"]["videoId"],
                "title":       item["snippet"]["title"],
                "published_at": item["snippet"]["publishedAt"],
                "channel_name": item["snippet"]["channelTitle"],
            }
            for item in items
        ]
    except Exception as e:
        logger.warning(f"YouTube API fetch failed for channel {channel_id}: {e}")
        return []

def get_transcript(video_id: str) -> Optional[str]:
    """Fetch transcript using youtube-transcript-api (no API key needed)."""
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(
            video_id,
            languages=["en", "en-IN", "hi"],   # English first, then Hindi
        )
        return " ".join([t["text"] for t in transcript_list])
    except (NoTranscriptFound, TranscriptsDisabled):
        logger.debug(f"No transcript available for video {video_id}")
        return None
    except Exception as e:
        logger.warning(f"Transcript fetch failed for {video_id}: {e}")
        return None

def is_video_processed(video_id: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM youtube_videos WHERE video_id = %s AND processed_at IS NOT NULL",
                (video_id,)
            )
            return cur.fetchone() is not None

def save_video_insights(
    channel_id: str, video_id: str, title: str,
    published_at: str, transcript: str, insights: dict
) -> None:
    tickers = [i["ticker"] for i in insights.get("insights", [])]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO youtube_videos
                    (channel_id, video_id, title, published_at, transcript_raw,
                     insights, tickers_mentioned, processed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (video_id) DO UPDATE SET
                    insights = EXCLUDED.insights,
                    tickers_mentioned = EXCLUDED.tickers_mentioned,
                    processed_at = NOW()
                """,
                (
                    channel_id, video_id, title, published_at,
                    transcript[:20000],            # cap raw storage
                    Json(insights),
                    tickers,
                )
            )

def get_user_channels(user_id: str) -> list[dict]:
    with get_conn() as conn:
        from psycopg2.extras import RealDictCursor
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM youtube_channels WHERE user_id = %s AND is_active = TRUE",
                (user_id,)
            )
            return [dict(r) for r in cur.fetchall()]

# ── Core agent logic ──────────────────────────────────────────────────────────

def run_youtube_agent(user_id: str) -> dict:
    run_id = start_agent_run("youtube")
    audit("agent_run_started", user_id=user_id, resource_type="agent_run", resource_id=run_id)

    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        finish_agent_run(run_id, "failed", error_message="YOUTUBE_API_KEY not set")
        return {"status": "failed", "error": "YouTube API key not configured"}

    channels  = get_user_channels(user_id)
    tickers   = get_all_tickers(user_id)
    provider  = get_provider(os.getenv("LLM_PROVIDER", "gemini"), use_sonnet=False)

    if not channels:
        finish_agent_run(run_id, "success", alerts_fired=0)
        return {"status": "success", "message": "No YouTube channels configured"}

    alerts_fired = 0
    total_tokens = 0
    videos_processed = 0

    for channel in channels:
        channel_id   = channel["channel_id"]
        channel_name = channel["channel_name"]
        videos = get_channel_recent_videos(channel_id, api_key, max_results=3)

        for video in videos:
            video_id = video["video_id"]

            if is_video_processed(video_id):
                logger.debug(f"Already processed: {video_id}")
                continue

            transcript = get_transcript(video_id)
            if not transcript:
                continue

            # Step 1 — cheap relevance check (Haiku)
            rel_response = provider.complete(
                system_prompt=RELEVANCE_SYSTEM,
                user_prompt=build_relevance_prompt(video["title"], transcript, tickers),
                max_tokens=200,
                temperature=0.1,
            )
            total_tokens += rel_response.input_tokens + rel_response.output_tokens

            try:
                rel_raw = rel_response.content.strip()
                if rel_raw.startswith("```"):
                    rel_raw = rel_raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                last_brace = rel_raw.rfind("}")
                if last_brace != -1:
                    rel_raw = rel_raw[:last_brace + 1]
                relevance = json.loads(rel_raw)
            except json.JSONDecodeError:
                continue

            if not relevance.get("is_relevant"):
                # Save minimal record so we don't reprocess
                save_video_insights(channel_id, video_id, video["title"],
                                    video["published_at"], "", {"insights": [], "skipped": True})
                continue

            mentioned = relevance.get("mentioned_tickers", [])
            logger.info(f"Relevant video: '{video['title']}' mentions {mentioned}")

            # Step 2 — full insight extraction (use full/pro model tier for quality)
            deep_provider = get_provider(os.getenv("LLM_PROVIDER", "gemini"), use_sonnet=True)
            ins_response = deep_provider.complete(
                system_prompt=INSIGHT_SYSTEM,
                user_prompt=build_insight_prompt(video["title"], transcript, mentioned),
                max_tokens=2048,
                temperature=0.2,
            )
            total_tokens += ins_response.input_tokens + ins_response.output_tokens

            try:
                ins_raw = ins_response.content.strip()
                if ins_raw.startswith("```"):
                    ins_raw = ins_raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                last_brace = ins_raw.rfind("}")
                if last_brace != -1:
                    ins_raw = ins_raw[:last_brace + 1]
                insights = json.loads(ins_raw)
            except json.JSONDecodeError:
                continue

            save_video_insights(
                channel_id, video_id, video["title"],
                video["published_at"], transcript, insights
            )
            videos_processed += 1

            # Fire alert if strong bearish signal on a holding
            for insight in insights.get("insights", []):
                if (insight.get("stance") == "bearish"
                        and insight.get("confidence") == "high"):
                    write_alert(
                        user_id=user_id,
                        agent_type="youtube",
                        severity="warning",
                        title=f"{channel_name} is bearish on {insight['ticker']}",
                        body=f"{insight['summary']} — from: \"{video['title']}\"",
                        ticker=insight["ticker"],
                        llm_provider=ins_response.provider,
                        raw_llm_output=insights,
                        data_sources={"video_id": video_id, "channel": channel_name},
                    )
                    alerts_fired += 1

    finish_agent_run(run_id, "success", alerts_fired=alerts_fired, tokens_used=total_tokens)
    return {
        "status": "success",
        "videos_processed": videos_processed,
        "alerts_fired": alerts_fired,
        "tokens_used": total_tokens,
    }


class RunRequest(BaseModel):
    user_id: str

@app.post("/run")
async def trigger_run(request: RunRequest, x_scheduler_secret: Optional[str] = Header(None)):
    if SCHEDULER_SECRET and x_scheduler_secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return run_youtube_agent(request.user_id)

@app.get("/health")
async def health():
    return {"status": "ok", "agent": "youtube"}
