// frontend/src/screens/StockDetailScreen.tsx
import { useEffect, useState, useCallback } from "react";
import { portfolio, alerts as alertsApi, HoldingDetail, NewsArticle, YouTubeVideo } from "../api";

interface Props {
  ticker: string;
  onBack: () => void;
  onAskAdvisor: (question: string) => void;
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: "#E24B4A", warning: "#EF9F27",
  info: "#378ADD", opportunity: "#1D9E75",
};

function timeAgo(isoString: string): string {
  const diff = Date.now() - new Date(isoString).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

const STANCE_COLOR: Record<string, string> = {
  bullish: "#1D9E75", bearish: "#E24B4A", neutral: "#888",
};

export function StockDetailScreen({ ticker, onBack, onAskAdvisor }: Props) {
  const [data, setData] = useState<HoldingDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedAlert, setExpandedAlert] = useState<string | null>(null);
  const [news, setNews] = useState<NewsArticle[]>([]);
  const [newsLoading, setNewsLoading] = useState(true);
  const [ytVideos, setYtVideos] = useState<YouTubeVideo[]>([]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setNewsLoading(true);

    portfolio.getHoldingDetail(ticker)
      .then(d => {
        setData({ ...d, alerts: d.alerts ?? [] });
        setLoading(false);
      })
      .catch(e => { setError(e.message); setLoading(false); });

    portfolio.getNews(ticker)
      .then(r => { setNews(r.articles ?? []); setNewsLoading(false); })
      .catch(() => setNewsLoading(false));

    portfolio.getYouTubeInsights(ticker)
      .then(r => setYtVideos(r.videos ?? []))
      .catch(() => {});
  }, [ticker]);

  const handleDismiss = useCallback(async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    await alertsApi.dismiss(id).catch(() => {});
    setData(prev => prev ? { ...prev, alerts: prev.alerts.filter(a => a.id !== id) } : prev);
  }, []);

  const handleMarkRead = useCallback(async (id: string) => {
    await alertsApi.markRead(id).catch(() => {});
    setData(prev => prev ? {
      ...prev,
      alerts: prev.alerts.map(a => a.id === id ? { ...a, is_read: true } : a),
    } : prev);
  }, []);

  if (loading) return (
    <div className="screen">
      <button className="back-btn" onClick={onBack}>&larr; Portfolio</button>
      <div className="screen-loading"><div className="spinner" /></div>
    </div>
  );

  if (error || !data) return (
    <div className="screen">
      <button className="back-btn" onClick={onBack}>&larr; Portfolio</button>
      <div className="screen-error">{error ?? "Failed to load stock details"}</div>
    </div>
  );

  const h = data.holding;
  const pnl = data.current_value - data.total_invested;
  const pnlPct = data.total_invested > 0 ? (pnl / data.total_invested) * 100 : 0;
  const unreadAlerts = data.alerts.filter(a => !a.is_read).length;

  const quickQuestions = [
    `What's the outlook for ${ticker}? Should I hold, buy more, or reduce?`,
    `How does ${ticker} fit in my overall portfolio allocation?`,
    `What are the key risks for ${ticker} right now?`,
    `Should I buy ${ticker} in tranches? What would be a good entry strategy?`,
    `How would a 15% drop in ${ticker} impact my portfolio?`,
  ];

  return (
    <div className="screen stock-detail">
      <button className="back-btn" onClick={onBack}>&larr; Portfolio</button>

      {/* Stock header */}
      <div className="stock-header-card">
        <div className="stock-header-top">
          <div>
            <h1 className="stock-ticker">{h.ticker}</h1>
            <div className="stock-company">{h.company_name || h.ticker}</div>
            <div className="stock-meta">
              <span className="stock-exchange">{h.exchange}</span>
              {h.sector && <span className="stock-sector">{h.sector}</span>}
            </div>
          </div>
          <div className="stock-price-block">
            <div className="stock-current-price">
              ₹{(h.last_price ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 2 })}
            </div>
            <div className={`stock-pnl-badge ${pnl >= 0 ? "positive" : "negative"}`}>
              {pnl >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
            </div>
          </div>
        </div>
      </div>

      {/* Key metrics */}
      <div className="stock-metrics-grid">
        <div className="metric-card">
          <div className="metric-label">Quantity</div>
          <div className="metric-value">{h.quantity}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Avg Cost</div>
          <div className="metric-value">₹{h.avg_cost.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Invested</div>
          <div className="metric-value">₹{data.total_invested.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Current Value</div>
          <div className="metric-value">₹{data.current_value.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">P&L</div>
          <div className={`metric-value ${pnl >= 0 ? "positive" : "negative"}`}>
            {pnl >= 0 ? "+" : ""}₹{Math.abs(pnl).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
          </div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Returns</div>
          <div className={`metric-value ${pnlPct >= 0 ? "positive" : "negative"}`}>
            {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
          </div>
        </div>
      </div>

      {/* Alerts section */}
      <div className="card">
        <div className="stock-section-header">
          <h2 className="card-title">
            Alerts {unreadAlerts > 0 && <span className="unread-count">{unreadAlerts}</span>}
          </h2>
        </div>
        {data.alerts.length === 0 ? (
          <div className="stock-empty">No alerts for {ticker} yet</div>
        ) : (
          <div className="stock-alert-list">
            {data.alerts.map(a => (
              <div
                key={a.id}
                className={`stock-alert-item ${a.is_read ? "read" : "unread"}`}
                onClick={() => {
                  if (!a.is_read) handleMarkRead(a.id);
                  setExpandedAlert(expandedAlert === a.id ? null : a.id);
                }}
              >
                <div className="stock-alert-header">
                  <span className="alert-badge" style={{ background: SEVERITY_COLORS[a.severity] }}>
                    {a.severity.toUpperCase()}
                  </span>
                  <span className="stock-alert-agent">{a.agent_type}</span>
                  <span className="alert-time">{timeAgo(a.created_at)}</span>
                  <button
                    className="dismiss-btn"
                    onClick={(e) => handleDismiss(a.id, e)}
                    title="Dismiss"
                  >&times;</button>
                </div>
                <div className="stock-alert-title">{a.title}</div>
                {expandedAlert === a.id && (
                  <div className="stock-alert-body">{a.body}</div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Live news */}
      <div className="card">
        <h2 className="card-title">Latest News</h2>
        {newsLoading ? (
          <div className="stock-empty"><div className="spinner" style={{ margin: "8px auto" }} /></div>
        ) : news.length === 0 ? (
          <div className="stock-empty">No recent news found for {ticker}</div>
        ) : (
          <div className="news-list">
            {news.map((a, i) => (
              <a key={i} href={a.url} target="_blank" rel="noopener noreferrer" className="news-item">
                <div className="news-title">{a.title}</div>
                <div className="news-meta">
                  <span className="news-source">{a.source}</span>
                  <span className="news-time">{timeAgo(a.published_at)}</span>
                </div>
                {a.description && <div className="news-desc">{a.description}</div>}
              </a>
            ))}
          </div>
        )}
      </div>

      {/* YouTube insights */}
      {ytVideos.length > 0 && (
        <div className="card">
          <h2 className="card-title">YouTube Insights</h2>
          <div className="yt-list">
            {ytVideos.map(v => (
              <a key={v.video_id} href={v.url} target="_blank" rel="noopener noreferrer" className="yt-item">
                <div className="yt-thumb">▶</div>
                <div className="yt-info">
                  <div className="yt-title">{v.title}</div>
                  <div className="yt-meta">{timeAgo(v.published_at)}</div>
                  {v.insight && (
                    <div className="yt-stance" style={{ color: STANCE_COLOR[v.insight.stance] }}>
                      {v.insight.stance.toUpperCase()} · {v.insight.confidence} confidence
                    </div>
                  )}
                  {v.insight?.summary && <div className="yt-summary">{v.insight.summary}</div>}
                </div>
              </a>
            ))}
          </div>
        </div>
      )}

      {/* Quick advisor actions */}
      <div className="card">
        <h2 className="card-title">Ask Advisor about {ticker}</h2>
        <div className="stock-quick-actions">
          {quickQuestions.map(q => (
            <button
              key={q}
              className="stock-quick-btn"
              onClick={() => onAskAdvisor(q)}
            >
              {q}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
