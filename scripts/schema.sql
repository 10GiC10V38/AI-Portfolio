-- ============================================================
-- Portfolio AI — Database Schema
-- Run on Neon (production) and local Postgres (development)
-- ============================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Users ────────────────────────────────────────────────────────────────────
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    mfa_secret      TEXT,                          -- TOTP secret, encrypted at app level
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

-- ── Brokerage connections ────────────────────────────────────────────────────
-- API keys are NEVER stored here — they live in GCP Secret Manager.
-- This table only stores the reference name and metadata.
CREATE TABLE brokerage_connections (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    broker_name     TEXT NOT NULL,                 -- 'zerodha' | 'ibkr' | 'alpaca'
    secret_ref      TEXT NOT NULL,                 -- GCP Secret Manager path only
    is_read_only    BOOLEAN NOT NULL DEFAULT TRUE, -- enforced — never false
    connected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_synced_at  TIMESTAMPTZ,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

-- ── Portfolio holdings ────────────────────────────────────────────────────────
CREATE TABLE holdings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL,
    exchange        TEXT NOT NULL,                 -- 'NSE' | 'BSE' | 'NASDAQ' | 'NYSE'
    company_name    TEXT,
    sector          TEXT,
    quantity        NUMERIC(18, 4) NOT NULL,
    avg_cost        NUMERIC(18, 4) NOT NULL,       -- average cost basis
    currency        TEXT NOT NULL DEFAULT 'INR',
    last_price      NUMERIC(18, 4),
    last_updated_at TIMESTAMPTZ,
    UNIQUE(user_id, ticker, exchange)
);

-- ── Sector allocation targets (for rebalancing advisor) ──────────────────────
CREATE TABLE allocation_targets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sector          TEXT NOT NULL,
    target_pct      NUMERIC(5, 2) NOT NULL,        -- e.g. 25.00 for 25%
    UNIQUE(user_id, sector)
);

-- ── Alerts ───────────────────────────────────────────────────────────────────
CREATE TABLE alerts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_type      TEXT NOT NULL,                 -- 'news'|'fundamentals'|'technical'|'macro'|'youtube'
    ticker          TEXT,                          -- NULL = portfolio-level alert
    severity        TEXT NOT NULL,                 -- 'critical'|'warning'|'info'|'opportunity'
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    confidence_pct  INTEGER,                       -- 0-100, NULL until Phase 2 multi-LLM
    llm_provider    TEXT NOT NULL DEFAULT 'claude',
    raw_llm_output  JSONB,                         -- full LLM response, for audit
    data_sources    JSONB,                         -- what data was fed to LLM
    is_read         BOOLEAN NOT NULL DEFAULT FALSE,
    is_dismissed    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_alerts_user_created ON alerts(user_id, created_at DESC);
CREATE INDEX idx_alerts_ticker ON alerts(ticker);
CREATE INDEX idx_alerts_unread ON alerts(user_id, is_read) WHERE is_read = FALSE;

-- ── Portfolio chat history (advisor agent) ────────────────────────────────────
CREATE TABLE chat_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id      UUID NOT NULL,                 -- groups messages into a conversation
    role            TEXT NOT NULL,                 -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    context_snapshot JSONB,                        -- portfolio state at time of message
    llm_provider    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chat_session ON chat_messages(session_id, created_at);

-- ── YouTube channels to monitor ───────────────────────────────────────────────
CREATE TABLE youtube_channels (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel_id      TEXT NOT NULL,                 -- YouTube channel ID
    channel_name    TEXT NOT NULL,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE(user_id, channel_id)
);

-- ── YouTube processed videos ──────────────────────────────────────────────────
CREATE TABLE youtube_videos (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id      TEXT NOT NULL,
    video_id        TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    published_at    TIMESTAMPTZ NOT NULL,
    transcript_raw  TEXT,
    insights        JSONB,                         -- extracted ticker mentions + sentiment
    tickers_mentioned TEXT[],                      -- for fast filtering
    processed_at    TIMESTAMPTZ
);

CREATE INDEX idx_youtube_tickers ON youtube_videos USING GIN(tickers_mentioned);

-- ── Agent run log ─────────────────────────────────────────────────────────────
CREATE TABLE agent_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_type      TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    status          TEXT NOT NULL,                 -- 'running'|'success'|'failed'
    alerts_fired    INTEGER DEFAULT 0,
    tokens_used     INTEGER,
    error_message   TEXT,
    metadata        JSONB
);

-- ── Immutable audit log (append-only — never UPDATE or DELETE) ────────────────
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID REFERENCES users(id),
    action          TEXT NOT NULL,                 -- 'login'|'alert_read'|'chat'|'agent_run' etc.
    resource_type   TEXT,
    resource_id     TEXT,
    ip_address      INET,
    user_agent      TEXT,
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Audit log is append-only — block updates and deletes via trigger
CREATE OR REPLACE FUNCTION block_audit_modifications()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only — modifications not permitted';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER enforce_audit_immutability
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION block_audit_modifications();

-- ── Push notification subscriptions ──────────────────────────────────────────
CREATE TABLE push_subscriptions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform        TEXT NOT NULL,                 -- 'fcm'|'apns'|'web'
    token           TEXT NOT NULL,
    device_label    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE(user_id, token)
);

-- ── User preferences ──────────────────────────────────────────────────────────
CREATE TABLE user_preferences (
    user_id                 UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    alert_severity_filter   TEXT[] DEFAULT ARRAY['critical','warning','info','opportunity'],
    polling_paused          BOOLEAN DEFAULT FALSE,
    email_alerts_enabled    BOOLEAN DEFAULT TRUE,
    push_alerts_enabled     BOOLEAN DEFAULT TRUE,
    quiet_hours_start       TIME,
    quiet_hours_end         TIME,
    timezone                TEXT DEFAULT 'Asia/Kolkata',
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);
