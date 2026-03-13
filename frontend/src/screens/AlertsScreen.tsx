// frontend/src/screens/AlertsScreen.tsx
import { useEffect, useState } from "react";
import { alerts, Alert } from "../api";

const SEVERITY_COLORS: Record<string, string> = {
  critical:    "#E24B4A",
  warning:     "#EF9F27",
  info:        "#378ADD",
  opportunity: "#1D9E75",
};

const SEVERITY_LABELS: Record<string, string> = {
  critical:    "CRITICAL",
  warning:     "WARNING",
  info:        "INFO",
  opportunity: "OPPORTUNITY",
};

function AlertCard({ alert, onRead }: { alert: Alert; onRead: (id: string) => void }) {
  const color = SEVERITY_COLORS[alert.severity] ?? "#378ADD";
  const ago   = timeAgo(alert.created_at);

  return (
    <div
      className={`alert-card ${alert.is_read ? "read" : "unread"}`}
      style={{ borderLeftColor: color }}
      onClick={() => !alert.is_read && onRead(alert.id)}
    >
      <div className="alert-header">
        <span className="alert-badge" style={{ background: color }}>
          {SEVERITY_LABELS[alert.severity] ?? alert.severity.toUpperCase()}
        </span>
        <span className="alert-agent">{alert.agent_type}</span>
        {alert.ticker && <span className="alert-ticker">{alert.ticker}</span>}
        <span className="alert-time">{ago}</span>
        {!alert.is_read && <span className="alert-dot" />}
      </div>
      <div className="alert-title">{alert.title}</div>
      <div className="alert-body">{alert.body}</div>
    </div>
  );
}

export function AlertsScreen() {
  const [data, setData]       = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [filter, setFilter]   = useState<"all" | "unread">("all");

  useEffect(() => {
    alerts.getAll(100)
      .then(d  => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, []);

  const handleRead = async (id: string) => {
    await alerts.markRead(id).catch(() => {});
    setData(prev => prev.map(a => a.id === id ? { ...a, is_read: true } : a));
  };

  const displayed   = filter === "unread" ? data.filter(a => !a.is_read) : data;
  const unreadCount = data.filter(a => !a.is_read).length;

  if (loading) return <div className="screen-loading"><div className="spinner" /></div>;
  if (error)   return <div className="screen-error">Failed to load: {error}</div>;

  return (
    <div className="screen">
      <div className="screen-header">
        <h1>Alerts</h1>
        <div className="filter-pills">
          <button
            className={`pill ${filter === "all" ? "active" : ""}`}
            onClick={() => setFilter("all")}
          >All ({data.length})</button>
          <button
            className={`pill ${filter === "unread" ? "active" : ""}`}
            onClick={() => setFilter("unread")}
          >Unread ({unreadCount})</button>
        </div>
      </div>

      {displayed.length === 0 ? (
        <div className="empty-state">
          {filter === "unread" ? "No unread alerts." : "No alerts yet — agents are monitoring your portfolio."}
        </div>
      ) : (
        <div className="alert-list">
          {displayed.map(a => (
            <AlertCard key={a.id} alert={a} onRead={handleRead} />
          ))}
        </div>
      )}
    </div>
  );
}

function timeAgo(isoString: string): string {
  const diff = Date.now() - new Date(isoString).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1)  return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24)  return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}
