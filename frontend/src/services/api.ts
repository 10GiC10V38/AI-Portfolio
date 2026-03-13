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
    const err = await resp.json().catch(() => ({ error: resp.statusText }));
    throw new Error(err.error ?? `Request failed: ${resp.status}`);
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

export const portfolio = {
  getHoldings: () => request<Holding[]>("/portfolio/holdings"),
};

// ── Alerts ────────────────────────────────────────────────────────────────────

export const alerts = {
  getAll: (limit = 50) => request<Alert[]>(`/alerts?limit=${limit}`),

  markRead: (id: string) =>
    request<{ success: boolean }>(`/alerts/${id}/read`, { method: "PATCH" }),
};

// ── Chat ──────────────────────────────────────────────────────────────────────

export const chat = {
  send: (message: string, sessionId: string) =>
    request<{ reply: string; session_id: string }>("/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id: sessionId }),
    }),
};
