"""
agents/zerodha_sync/main.py — Manual Login Flow

Daily workflow (30 seconds):
1. Browser: https://kite.trade/connect/login?api_key=YOUR_API_KEY&v=3
2. Log in on Zerodha's page with credentials + TOTP
3. Copy ?request_token=xxxx from the redirect URL
4. Portfolio AI app → Admin → paste token → Sync
5. Done — all agents now have your live holdings

Only needs ZERODHA_API_KEY and ZERODHA_API_SECRET.
No password, no TOTP secret stored anywhere.
"""

from __future__ import annotations
import os, sys, json, logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from kiteconnect import KiteConnect

sys.path.insert(0, "/app/shared")
from db.connection import init_pool, get_conn, start_agent_run, finish_agent_run, audit

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
app = FastAPI(title="Zerodha Sync Agent")
SCHEDULER_SECRET = os.getenv("SCHEDULER_SECRET", "")


def _req(key: str) -> str:
    v = os.getenv(key)
    if not v:
        raise EnvironmentError(f"Required env var not set: {key}")
    return v


def load_secrets() -> dict:
    source = os.getenv("SECRETS_SOURCE", "env")
    if source == "env":
        return {
            "api_key":    _req("ZERODHA_API_KEY"),
            "api_secret": _req("ZERODHA_API_SECRET"),
        }
    if source == "gcp":
        from google.cloud import secretmanager
        client  = secretmanager.SecretManagerServiceClient()
        project = os.environ["GCP_PROJECT_ID"]
        def access(name):
            path = f"projects/{project}/secrets/{name}/versions/latest"
            return client.access_secret_version(request={"name": path}).payload.data.decode()
        return {"api_key": access("zerodha-api-key"), "api_secret": access("zerodha-api-secret")}
    raise ValueError(f"Unknown SECRETS_SOURCE: {source}")


def _enrich(ticker: str, exchange: str) -> tuple[Optional[str], Optional[str]]:
    """Company name + sector from yfinance. Graceful fallback if unavailable."""
    try:
        import yfinance as yf
        info   = yf.Ticker(f"{ticker}{'.NS' if exchange == 'NSE' else '.BO'}").info
        return (info.get("longName") or info.get("shortName")), \
               (info.get("sector") or info.get("industry"))
    except Exception:
        return None, None


def sync_holdings(user_id: str, kite: KiteConnect) -> int:
    holdings = kite.holdings()
    logger.info(f"Fetched {len(holdings)} holdings from Zerodha")

    with get_conn() as conn:
        with conn.cursor() as cur:
            for h in holdings:
                ticker, exchange = h["tradingsymbol"], h["exchange"]
                company, sector  = _enrich(ticker, exchange)
                cur.execute("""
                    INSERT INTO holdings
                        (user_id, ticker, exchange, company_name, sector,
                         quantity, avg_cost, currency, last_price, last_updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'INR',%s,NOW())
                    ON CONFLICT (user_id, ticker, exchange) DO UPDATE SET
                        quantity        = EXCLUDED.quantity,
                        avg_cost        = EXCLUDED.avg_cost,
                        last_price      = EXCLUDED.last_price,
                        company_name    = COALESCE(EXCLUDED.company_name, holdings.company_name),
                        sector          = COALESCE(EXCLUDED.sector, holdings.sector),
                        last_updated_at = NOW()
                """, (user_id, ticker, exchange, company, sector,
                      h["quantity"], h["average_price"], h["last_price"]))

            # Remove sold positions
            if holdings:
                live = [(h["tradingsymbol"], h["exchange"]) for h in holdings]
                cur.execute(
                    "DELETE FROM holdings WHERE user_id=%s AND (ticker,exchange) NOT IN %s",
                    (user_id, tuple(live))
                )
                if cur.rowcount > 0:
                    logger.info(f"Removed {cur.rowcount} sold holdings")

    return len(holdings)


def store_token(user_id: str, access_token: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO brokerage_connections
                    (user_id, broker_name, secret_ref, is_read_only, last_synced_at)
                VALUES (%s,'zerodha',%s,TRUE,NOW())
                ON CONFLICT (user_id, broker_name) DO UPDATE SET
                    secret_ref=EXCLUDED.secret_ref, last_synced_at=NOW()
            """, (user_id, access_token))


def run_sync(user_id: str, request_token: str) -> dict:
    run_id = start_agent_run("zerodha_sync")
    audit("zerodha_sync_started", user_id=user_id, resource_type="agent_run", resource_id=run_id)
    try:
        secrets      = load_secrets()
        kite         = KiteConnect(api_key=secrets["api_key"])
        session_data = kite.generate_session(request_token, api_secret=secrets["api_secret"])
        kite.set_access_token(session_data["access_token"])

        profile = kite.profile()
        logger.info(f"Connected: {profile['user_name']} ({profile['user_id']})")

        count = sync_holdings(user_id, kite)
        store_token(user_id, session_data["access_token"])

        audit("zerodha_sync_completed", user_id=user_id,
              resource_type="brokerage", resource_id="zerodha",
              metadata={"holdings_synced": count})
        finish_agent_run(run_id, "success")

        return {
            "status":          "success",
            "account":         profile["user_name"],
            "holdings_synced": count,
            "message":         f"Synced {count} holdings. Agents now have live data.",
        }
    except Exception as e:
        logger.error(f"Sync failed: {e}", exc_info=True)
        finish_agent_run(run_id, "failed", error_message=str(e))
        raise


# ── Endpoints ─────────────────────────────────────────────────────────────────

class SyncRequest(BaseModel):
    user_id:       str
    request_token: str   # from the redirect URL: ?request_token=xxxx


@app.post("/sync")
async def trigger_sync(req: SyncRequest, x_scheduler_secret: Optional[str] = Header(None)):
    """Called by the frontend Admin page after manual Kite login."""
    if SCHEDULER_SECRET and x_scheduler_secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return run_sync(req.user_id, req.request_token)


@app.get("/login-url")
async def get_login_url():
    """Returns the Kite login URL. Frontend uses this to build the login button."""
    kite = KiteConnect(api_key=load_secrets()["api_key"])
    return {"login_url": kite.login_url()}


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "zerodha_sync"}


if __name__ == "__main__":
    import uvicorn
    init_pool()
    uvicorn.run(app, host="0.0.0.0", port=8003)
