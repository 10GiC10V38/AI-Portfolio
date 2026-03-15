// frontend/src/App.tsx
import { useState, useEffect } from "react";
import { PortfolioScreen } from "./screens/PortfolioScreen";
import { AlertsScreen }    from "./screens/AlertsScreen";
import { ChatScreen }      from "./screens/ChatScreen";
import { StockDetailScreen } from "./screens/StockDetailScreen";
import { auth }            from "./api";
import { zerodha }         from "./services/api";
import "./index.css";

type Tab = "portfolio" | "alerts" | "chat";

interface StockView {
  ticker: string;
}

function LoginScreen({ onLogin }: { onLogin: () => void }) {
  const [email, setEmail]       = useState("");
  const [password, setPassword] = useState("");
  const [error, setError]       = useState<string | null>(null);
  const [loading, setLoading]   = useState(false);
  const [isRegister, setIsRegister] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const action = isRegister ? auth.register : auth.login;
      const resp = await action(email, password);
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
            placeholder="Password (min 8 characters)"
            value={password}
            onChange={e => setPassword(e.target.value)}
            required
            minLength={8}
            autoComplete={isRegister ? "new-password" : "current-password"}
          />
          {error && <div className="login-error">{error}</div>}
          <button type="submit" disabled={loading}>
            {loading ? (isRegister ? "Creating account…" : "Signing in…") : (isRegister ? "Create Account" : "Sign In")}
          </button>
          <button
            type="button"
            className="login-toggle"
            onClick={() => { setIsRegister(!isRegister); setError(null); }}
          >
            {isRegister ? "Already have an account? Sign in" : "New here? Create an account"}
          </button>
        </form>
      </div>
    </div>
  );
}

function NavBar({ active, onChange, unreadAlerts }: { active: Tab; onChange: (t: Tab) => void; unreadAlerts: number }) {
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
          <span className="nav-icon">
            {t.icon}
            {t.id === "alerts" && unreadAlerts > 0 && (
              <span className="nav-badge">{unreadAlerts > 99 ? "99+" : unreadAlerts}</span>
            )}
          </span>
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
  const [stockView, setStockView]   = useState<StockView | null>(null);
  const [advisorPrefill, setAdvisorPrefill] = useState<string | null>(null);
  const [unreadAlerts, setUnreadAlerts] = useState(0);

  useEffect(() => {
    setLoggedIn(auth.isLoggedIn());
  }, []);

  // Handle Kite OAuth callback
  useEffect(() => {
    if (!auth.isLoggedIn()) return;
    const params = new URLSearchParams(window.location.search);
    const requestToken = params.get("request_token");
    const status       = params.get("status");
    if (requestToken && status === "success") {
      setKiteSyncing(true);
      window.history.replaceState({}, "", window.location.pathname);
      zerodha.sync(requestToken)
        .catch(() => {})
        .finally(() => { setKiteSyncing(false); setPortfolioKey(k => k + 1); });
    }
  }, [loggedIn]);

  const handleStockSelect = (ticker: string) => {
    setStockView({ ticker });
  };

  const handleAskAdvisor = (question: string) => {
    setAdvisorPrefill(question);
    setStockView(null);
    setActiveTab("chat");
  };

  const handleTabChange = (t: Tab) => {
    setStockView(null);
    setActiveTab(t);
  };

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
        {stockView ? (
          <StockDetailScreen
            ticker={stockView.ticker}
            onBack={() => setStockView(null)}
            onAskAdvisor={handleAskAdvisor}
          />
        ) : (
          <>
            {activeTab === "portfolio" && (
              <PortfolioScreen
                key={portfolioKey}
                onConnectKite={() => window.location.href = zerodha.loginUrl()}
                onStockSelect={handleStockSelect}
              />
            )}
            {activeTab === "alerts" && (
              <AlertsScreen
                onStockSelect={handleStockSelect}
                onUnreadCount={setUnreadAlerts}
              />
            )}
            {activeTab === "chat" && (
              <ChatScreen
                prefillMessage={advisorPrefill}
                onPrefillConsumed={() => setAdvisorPrefill(null)}
              />
            )}
          </>
        )}
      </main>

      <NavBar active={activeTab} onChange={handleTabChange} unreadAlerts={unreadAlerts} />
    </div>
  );
}
