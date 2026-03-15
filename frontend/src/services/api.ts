/**
 * frontend/src/services/api.ts
 * Typed API client for the Go API gateway.
 * All requests include the JWT from localStorage.
 */

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8080";

// ── Types ──────────────────────────────────────────────────────────────────────

export interface Holding {
  ticker:         string;
  exchange:       string;
  company_name:   string;
  sector:         string;
  quantity:       number;
  avg_cost:       number;
  currency:       string;
  last_price:     number | null;
  unrealized_pct: number | null;
  unrealized_pnl: number | null;
}

export interface Alert {
  id:             string;
  agent_type:     string;
  ticker:         string | null;
  severity:       "critical" | "warning" | "info" | "opportunity";
  title:          string;
  body:           string;
  confidence_pct: number | null;
  is_read:        boolean;
  created_at:     string;
}

export interface ChatMessage {
  role:    "user" | "assistant";
  content: string;
}

export interface AuthResponse {
  token:   string;
  user_id: string;
}

// ── HTTP helpers ──────────────────────────────────────────────────────────────

function getToken(): string | null {
  return localStorage.getItem("token");
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> ?? {}),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const resp = await fetch(`${BASE_URL}${path}`, { ...options, headers });

  if (resp.status === 401) {
    localStorage.removeItem("token");
    window.location.href = "/login";
    throw new Error("Session expired — please log in again");
  }

  if (!resp.ok) {
    const err = await resp.json().catch(() => null);
    throw new Error(err?.error ?? `Request failed: ${resp.status}`);
  }

  return resp.json() as Promise<T>;
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export const auth = {
  login: (email: string, password: string) =>
    request<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  register: (email: string, password: string) =>
    request<AuthResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  logout: () => {
    localStorage.removeItem("token");
    localStorage.removeItem("user_id");
    window.location.href = "/login";
  },

  isLoggedIn: () => !!getToken(),
};

// ── Portfolio ─────────────────────────────────────────────────────────────────

export interface Fundamentals {
  pe_ratio?:       string | null;
  eps?:            string | null;
  market_cap?:     string | null;
  week_52_high?:   string | null;
  week_52_low?:    string | null;
  dividend_yield?: string | null;
  beta?:           string | null;
  profit_margin?:  string | null;
  book_value?:     string | null;
  pb_ratio?:       string | null;
  description?:    string | null;
}

export interface HoldingDetail {
  holding:        Holding & { last_updated: string };
  alerts:         Alert[];
  total_invested: number;
  current_value:  number;
  fundamentals:   Fundamentals | null;
}

export interface NewsArticle {
  title:        string;
  description:  string;
  url:          string;
  source:       string;
  published_at: string;
}

export interface YouTubeVideo {
  video_id:     string;
  title:        string;
  published_at: string;
  url:          string;
  insight: {
    stance:      "bullish" | "bearish" | "neutral";
    confidence:  string;
    summary:     string;
    key_points:  string[];
  } | null;
}

export const portfolio = {
  getHoldings: () => request<Holding[]>("/portfolio/holdings"),

  getHoldingDetail: (ticker: string) =>
    request<HoldingDetail>(`/portfolio/holdings/${encodeURIComponent(ticker)}`),

  getNews: (ticker: string) =>
    request<{ articles: NewsArticle[]; ticker: string }>(`/portfolio/news/${encodeURIComponent(ticker)}`),

  getYouTubeInsights: (ticker: string) =>
    request<{ videos: YouTubeVideo[]; ticker: string }>(`/portfolio/youtube/${encodeURIComponent(ticker)}`),
};

// ── Zerodha ───────────────────────────────────────────────────────────────────

export const zerodha = {
  sync: (requestToken: string) =>
    request<{ synced: number; status: string }>("/admin/zerodha/sync", {
      method: "POST",
      body: JSON.stringify({ request_token: requestToken }),
    }),

  loginUrl: () => {
    const apiKey = import.meta.env.VITE_ZERODHA_API_KEY;
    return `https://kite.zerodha.com/connect/login?api_key=${apiKey}&v=3`;
  },
};

// ── Alerts ────────────────────────────────────────────────────────────────────

export const alerts = {
  getAll: (limit = 50) => request<Alert[]>(`/alerts?limit=${limit}`),

  getByTicker: (ticker: string) =>
    request<Alert[]>(`/alerts/ticker/${encodeURIComponent(ticker)}`),

  markRead: (id: string) =>
    request<{ success: boolean }>(`/alerts/${encodeURIComponent(id)}/read`, { method: "PATCH" }),

  dismiss: (id: string) =>
    request<{ success: boolean }>(`/alerts/${encodeURIComponent(id)}/dismiss`, { method: "PATCH" }),
};

// ── Chat ──────────────────────────────────────────────────────────────────────

export interface HistoryMessage {
  role:       "user" | "assistant";
  content:    string;
  created_at: string;
}

export const chat = {
  send: (message: string, sessionId: string) =>
    request<{ reply: string; session_id: string }>("/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id: sessionId }),
    }),

  getHistory: (sessionId: string) =>
    request<{ messages: HistoryMessage[] }>(`/chat/history?session_id=${sessionId}`),
};
