"""
agents/advisor/main.py — Portfolio Advisor Agent (Chat)
On-demand only — triggered by user chat via the API gateway.
Maintains per-session conversation history, has full access to portfolio state,
recent alerts, and all agent insights.
"""
import os, sys, json, logging, uuid, re

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)

def is_valid_uuid(val: str) -> bool:
    return bool(_UUID_RE.match(val))
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, "/app/shared")
from llm.client import get_provider
from db.connection import get_user_holdings, get_conn, audit
from psycopg2.extras import RealDictCursor, Json

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
app = FastAPI(title="Advisor Agent")

SYSTEM_PROMPT = """You are a personal portfolio advisor AI with deep knowledge of equity markets,
both Indian (NSE/BSE) and US (NASDAQ/NYSE). You have access to the user's current holdings,
recent alerts from our monitoring agents, and the ability to answer specific questions about
their portfolio.

Guidelines:
- Be direct and specific — reference actual holdings, not generic advice
- Acknowledge uncertainty when data is incomplete
- Never recommend leverage or derivatives unless explicitly asked
- Always note that your analysis is not regulated financial advice
- Respond conversationally but with analytical depth when needed"""


def get_recent_alerts(user_id: str, limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT agent_type, ticker, severity, title, body, created_at
                FROM alerts
                WHERE user_id = %s AND is_dismissed = FALSE
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]


def get_session_history(session_id: str, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT role, content FROM chat_messages
                WHERE session_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (session_id, limit),
            )
            rows = cur.fetchall()
            return [dict(r) for r in reversed(rows)]   # chronological order


def save_message(
    user_id: str, session_id: str, role: str,
    content: str, llm_provider: Optional[str] = None,
    context_snapshot: Optional[dict] = None,
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_messages
                    (user_id, session_id, role, content, llm_provider, context_snapshot)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id, session_id, role, content, llm_provider,
                    Json(context_snapshot) if context_snapshot else None,
                ),
            )


def build_context_snapshot(user_id: str) -> dict:
    holdings = get_user_holdings(user_id)
    alerts   = get_recent_alerts(user_id)

    def _serialize(v):
        if hasattr(v, 'isoformat'):          # datetime / date
            return v.isoformat()
        if hasattr(v, '__float__') and not isinstance(v, (int, str, bool, type(None))):
            return float(v)                  # Decimal
        return v

    serializable_holdings = [
        {k: _serialize(v) for k, v in h.items()}
        for h in holdings
    ]
    serializable_alerts = [
        {k: _serialize(v) for k, v in a.items()}
        for a in alerts
    ]

    total_value = sum(
        float(h.get("last_price") or h["avg_cost"]) * float(h["quantity"])
        for h in holdings
    )

    return {
        "holdings":      serializable_holdings,
        "total_value":   round(total_value, 2),
        "recent_alerts": serializable_alerts,
        "holdings_count": len(holdings),
    }


def build_context_message(context: dict) -> str:
    holdings_summary = "\n".join(
        f"  - {h['ticker']} ({h['exchange']}): {h['quantity']} shares @ avg ₹{h['avg_cost']}"
        + (f", current ₹{h['last_price']}" if h.get("last_price") else "")
        for h in context["holdings"]
    )

    alerts_summary = "\n".join(
        f"  [{a['severity'].upper()}] {a['agent_type']}: {a['title']}"
        for a in context["recent_alerts"][:5]
    ) or "  None"

    return f"""[Portfolio Context — injected automatically]
Total portfolio value: ₹{context['total_value']:,.2f}
Holdings ({context['holdings_count']}):
{holdings_summary}

Recent agent alerts:
{alerts_summary}
"""


def run_chat(user_id: str, session_id: str, user_message: str) -> dict:
    audit("chat_message", user_id=user_id, resource_type="session", resource_id=session_id)

    # Load context on first message of session or every time (always fresh)
    context = build_context_snapshot(user_id)
    history = get_session_history(session_id)

    # Build messages for Claude
    messages = []

    # Inject portfolio context as first user message if history is empty
    if not history:
        messages.append({
            "role": "user",
            "content": build_context_message(context),
        })
        messages.append({
            "role": "assistant",
            "content": "I have your portfolio context loaded. How can I help you?",
        })
    else:
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})

    # Add current user message
    messages.append({"role": "user", "content": user_message})

    # Save user message to DB
    save_message(user_id, session_id, "user", user_message, context_snapshot=context)

    # Chat uses the "full" model tier (gemini-2.0-flash or claude-haiku)
    # Set LLM_PROVIDER=claude in env to use Claude once you have API credits
    provider = get_provider(os.getenv("LLM_PROVIDER", "gemini"), use_sonnet=True)
    response = provider.complete_chat(
        system_prompt=SYSTEM_PROMPT,
        messages=messages,
        max_tokens=4096,
    )

    assistant_reply = response.content
    tokens = response.input_tokens + response.output_tokens

    save_message(user_id, session_id, "assistant", assistant_reply, llm_provider=response.provider)

    return {
        "reply":      assistant_reply,
        "session_id": session_id,
        "tokens_used": tokens,
    }


class ChatRequest(BaseModel):
    user_id: str
    message: str
    session_id: Optional[str] = None

@app.post("/chat")
async def chat(request: ChatRequest):
    sid = request.session_id
    session_id = sid if sid and is_valid_uuid(sid) else str(uuid.uuid4())
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    return run_chat(request.user_id, session_id, request.message.strip())

@app.get("/health")
async def health():
    return {"status": "ok", "agent": "advisor"}
