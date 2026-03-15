// frontend/src/screens/AlertsScreen.tsx
import { useEffect, useState, useMemo } from "react";
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

const SEVERITY_ORDER: Record<string, number> = {
  critical: 0, warning: 1, opportunity: 2, info: 3,
};

interface Props {
  onStockSelect?: (ticker: string) => void;
  onUnreadCount?: (count: number) => void;
}

function AlertCard({ alert, onRead, onDismiss, onTickerClick }: {
  alert: Alert;
  onRead: (id: string) => void;
  onDismiss: (id: string) => void;
  onTickerClick?: (ticker: string) => void;
}) {
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
        {alert.ticker && (
          <button
            className="alert-ticker"
            onClick={(e) => {
              e.stopPropagation();
              onTickerClick?.(alert.ticker!);
            }}
          >
            {alert.ticker}
          </button>
        )}
        <span className="alert-time">{ago}</span>
        {!alert.is_read && <span className="alert-dot" />}
        <button
          className="dismiss-btn"
          onClick={(e) => { e.stopPropagation(); onDismiss(alert.id); }}
          title="Dismiss"
        >&times;</button>
      </div>
      <div className="alert-title">{alert.title}</div>
      <div className="alert-body">{alert.body}</div>
    </div>
  );
}

export function AlertsScreen({ onStockSelect, onUnreadCount }: Props) {
  const [data, setData]       = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [filter, setFilter]   = useState<"all" | "unread" | "critical" | "warning" | "opportunity">("all");

  useEffect(() => {
    alerts.getAll(100)
      .then(d  => { setData(d ?? []); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, []);

  const unreadCount = useMemo(() => data.filter(a => !a.is_read).length, [data]);

  useEffect(() => {
    onUnreadCount?.(unreadCount);
  }, [unreadCount, onUnreadCount]);

  const handleRead = async (id: string) => {
    await alerts.markRead(id).catch(() => {});
    setData(prev => prev.map(a => a.id === id ? { ...a, is_read: true } : a));
  };

  const handleDismiss = async (id: string) => {
    await alerts.dismiss(id).catch(() => {});
    setData(prev => prev.filter(a => a.id !== id));
  };

  const displayed = useMemo(() => {
    let filtered = data;
    if (filter === "unread") {
      filtered = data.filter(a => !a.is_read);
    } else if (filter !== "all") {
      filtered = data.filter(a => a.severity === filter);
    }
    return filtered.sort((a, b) => {
      // Sort by severity first, then by date
      const sevDiff = (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9);
      if (sevDiff !== 0) return sevDiff;
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });
  }, [data, filter]);

  const criticalCount = data.filter(a => a.severity === "critical").length;
  const warningCount = data.filter(a => a.severity === "warning").length;
  const opportunityCount = data.filter(a => a.severity === "opportunity").length;

  if (loading) return <div className="screen-loading"><div className="spinner" /></div>;
  if (error)   return <div className="screen-error">Failed to load: {error}</div>;

  return (
    <div className="screen">
      <div className="screen-header">
        <h1>Alerts</h1>
        <div className="filter-pills">
          <button className={`pill ${filter === "all" ? "active" : ""}`} onClick={() => setFilter("all")}>
            All ({data.length})
          </button>
          <button className={`pill ${filter === "unread" ? "active" : ""}`} onClick={() => setFilter("unread")}>
            Unread ({unreadCount})
          </button>
          {criticalCount > 0 && (
            <button className={`pill pill-critical ${filter === "critical" ? "active" : ""}`} onClick={() => setFilter("critical")}>
              Critical ({criticalCount})
            </button>
          )}
          {warningCount > 0 && (
            <button className={`pill pill-warning ${filter === "warning" ? "active" : ""}`} onClick={() => setFilter("warning")}>
              Warning ({warningCount})
            </button>
          )}
          {opportunityCount > 0 && (
            <button className={`pill pill-opportunity ${filter === "opportunity" ? "active" : ""}`} onClick={() => setFilter("opportunity")}>
              Opportunity ({opportunityCount})
            </button>
          )}
        </div>
      </div>

      {displayed.length === 0 ? (
        <div className="empty-state">
          <div className="empty-icon">🔔</div>
          {filter === "unread"
            ? <p>All caught up! No unread alerts.</p>
            : filter !== "all"
            ? <p>No {filter} alerts.</p>
            : <p>No alerts yet — agents are monitoring your portfolio.</p>
          }
        </div>
      ) : (
        <div className="alert-list">
          {displayed.map(a => (
            <AlertCard
              key={a.id}
              alert={a}
              onRead={handleRead}
              onDismiss={handleDismiss}
              onTickerClick={onStockSelect}
            />
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
