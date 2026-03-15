"""
shared/db/connection.py

Database connection pool and common queries used by all agents.
Uses psycopg2 with a simple connection pool.
DATABASE_URL comes from env (local) or GCP Secret Manager (cloud).
"""
from __future__ import annotations
import os
import re
import logging
import uuid
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor, Json

# Input validation
_TICKER_RE = re.compile(r'^[A-Z0-9\-\.&_]{1,20}$')
_VALID_SEVERITIES = {"critical", "warning", "info", "opportunity"}

logger = logging.getLogger(__name__)

# ── Connection pool (initialised once per process) ────────────────────────────

_pool: Optional[pool.ThreadedConnectionPool] = None


def _resolve_database_url() -> str:
    """Get DATABASE_URL from env or GCP Secret Manager."""
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    if os.getenv("SECRETS_SOURCE") == "gcp":
        try:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
            project = os.environ["GCP_PROJECT_ID"]
            path = f"projects/{project}/secrets/database-url/versions/latest"
            resp = client.access_secret_version(request={"name": path})
            url = resp.payload.data.decode("UTF-8").strip()
            os.environ["DATABASE_URL"] = url  # cache for subsequent calls
            logger.info("DATABASE_URL loaded from GCP Secret Manager")
            return url
        except Exception as e:
            logger.error(f"Failed to load DATABASE_URL from GCP Secret Manager: {e}")
            raise
    raise EnvironmentError("DATABASE_URL environment variable is not set")


def _get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        db_url = _resolve_database_url()
        _pool = pool.ThreadedConnectionPool(minconn=1, maxconn=5, dsn=db_url)
        logger.info("Database connection pool created")
    return _pool


@contextmanager
def get_conn():
    """Context manager: borrows a connection from the pool and auto-commits."""
    p = _get_pool()
    conn = p.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


# ── Common queries ─────────────────────────────────────────────────────────────

def get_user_holdings(user_id: str) -> list[dict]:
    """Return all holdings for a user as a list of dicts."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT ticker, exchange, company_name, sector,
                       quantity, avg_cost, currency, last_price
                FROM holdings
                WHERE user_id = %s
                ORDER BY sector, ticker
                """,
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_all_tickers(user_id: str) -> list[str]:
    """Return just the ticker symbols for a user's holdings."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ticker FROM holdings WHERE user_id = %s",
                (user_id,),
            )
            return [row[0] for row in cur.fetchall()]


def write_alert(
    user_id: str,
    agent_type: str,
    severity: str,
    title: str,
    body: str,
    ticker: Optional[str],
    llm_provider: str,
    raw_llm_output: dict,
    data_sources: dict,
    confidence_pct: Optional[int] = None,
) -> str:
    """Insert an alert row and return its UUID."""
    # Validate severity
    if severity not in _VALID_SEVERITIES:
        raise ValueError(f"Invalid severity: {severity!r}")
    # Validate ticker format (reject potential XSS/injection)
    if ticker and not _TICKER_RE.match(ticker.upper()):
        logger.warning(f"Invalid ticker rejected: {ticker!r}")
        ticker = None
    elif ticker:
        ticker = ticker.upper()
    # Sanitize title and body length
    title = title[:500] if title else ""
    body = body[:5000] if body else ""
    alert_id = str(uuid.uuid4())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO alerts
                    (id, user_id, agent_type, ticker, severity, title, body,
                     confidence_pct, llm_provider, raw_llm_output, data_sources)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    alert_id, user_id, agent_type, ticker, severity,
                    title, body, confidence_pct, llm_provider,
                    Json(raw_llm_output), Json(data_sources),
                ),
            )
    logger.info(f"Alert written | id={alert_id} agent={agent_type} severity={severity}")
    return alert_id


def start_agent_run(agent_type: str) -> str:
    """Insert an agent_runs row with status=running. Returns run UUID."""
    run_id = str(uuid.uuid4())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_runs (id, agent_type, status)
                VALUES (%s, %s, 'running')
                """,
                (run_id, agent_type),
            )
    return run_id


def finish_agent_run(
    run_id: str,
    status: str,                       # 'success' | 'failed'
    alerts_fired: int = 0,
    tokens_used: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agent_runs
                SET status = %s,
                    completed_at = NOW(),
                    alerts_fired = %s,
                    tokens_used = %s,
                    error_message = %s
                WHERE id = %s
                """,
                (status, alerts_fired, tokens_used, error_message, run_id),
            )


def audit(
    action: str,
    user_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Append a row to the immutable audit_log table."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_log
                    (user_id, action, resource_type, resource_id, metadata)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    user_id, action, resource_type, resource_id,
                    Json(metadata) if metadata else None,
                ),
            )
