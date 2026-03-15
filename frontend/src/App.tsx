// frontend/src/App.tsx
import { useState, useEffect } from "react";
import { PortfolioScreen } from "./screens/PortfolioScreen";
import { AlertsScreen }    from "./screens/AlertsScreen";
import { ChatScreen }      from "./screens/ChatScreen";
import { auth }            from "./api";
import { zerodha }         from "./services/api";
import "./index.css";

type Tab = "portfolio" | "alerts" | "chat";

function LoginScreen({ onLogin }: { onLogin: () => void }) {
  const [email, setEmail]       = useState("");
  const [password, setPassword] = useState("");
  const [error, setError]       = useState<string | null>(null);
  const [loading, setLoading]   = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const resp = await auth.login(email, password);
      localStorage.setItem("token",   resp.token);
      localStorage.setItem("user_id", resp.user_id);
      onLogin();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-logo">📊</div>
        <h1>Portfolio AI</h1>
        <p className="login-subtitle">Your 24/7 portfolio intelligence system</p>
        <form onSubmit={handleSubmit} className="login-form">
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            required
            autoComplete="email"
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            required
            autoComplete="current-password"
          />
          {error && <div className="login-error">{error}</div>}
          <button type="submit" disabled={loading}>
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}

function NavBar({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  const tabs: { id: Tab; label: string; icon: string }[] = [
    { id: "portfolio", label: "Portfolio", icon: "📈" },
    { id: "alerts",    label: "Alerts",    icon: "🔔" },
    { id: "chat",      label: "Advisor",   icon: "💬" },
  ];
  return (
    <nav className="nav-bar">
      {tabs.map(t => (
        <button
          key={t.id}
          className={`nav-item ${active === t.id ? "active" : ""}`}
          onClick={() => onChange(t.id)}
        >
          <span className="nav-icon">{t.icon}</span>
          <span className="nav-label">{t.label}</span>
        </button>
      ))}
    </nav>
  );
}

export default function App() {
  const [loggedIn, setLoggedIn]     = useState(auth.isLoggedIn());
  const [activeTab, setActiveTab]   = useState<Tab>("portfolio");
  const [kitesyncing, setKiteSyncing] = useState(false);
  const [portfolioKey, setPortfolioKey] = useState(0);

  useEffect(() => {
    setLoggedIn(auth.isLoggedIn());
  }, []);

  // Handle Kite OAuth callback — detect ?request_token=xxx in URL after redirect
  useEffect(() => {
    if (!auth.isLoggedIn()) return;
    const params = new URLSearchParams(window.location.search);
    const requestToken = params.get("request_token");
    const status       = params.get("status");
    if (requestToken && status === "success") {
      setKiteSyncing(true);
      // Clean the URL immediately so refresh doesn't re-trigger
      window.history.replaceState({}, "", window.location.pathname);
      zerodha.sync(requestToken)
        .catch(() => {})
        .finally(() => { setKiteSyncing(false); setPortfolioKey(k => k + 1); });
    }
  }, [loggedIn]);

  if (!loggedIn) {
    return <LoginScreen onLogin={() => setLoggedIn(true)} />;
  }

  return (
    <div className="app-layout">
      <header className="app-header">
        <span className="app-logo">📊</span>
        <span className="app-title">Portfolio AI</span>
        <button className="logout-btn" onClick={auth.logout}>Sign out</button>
      </header>

      {kitesyncing && (
        <div className="sync-banner">Syncing holdings from Kite…</div>
      )}

      <main className="app-main">
        {activeTab === "portfolio" && <PortfolioScreen key={portfolioKey} onConnectKite={() => window.location.href = zerodha.loginUrl()} />}
        {activeTab === "alerts"    && <AlertsScreen />}
        {activeTab === "chat"      && <ChatScreen />}
      </main>

      <NavBar active={activeTab} onChange={setActiveTab} />
    </div>
  );
}
