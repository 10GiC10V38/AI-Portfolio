// frontend/src/screens/PortfolioScreen.tsx
import { useEffect, useState, useMemo } from "react";
import { portfolio, Holding } from "../api";

interface SectorGroup {
  sector: string;
  totalValue: number;
  pct: number;
  holdings: Holding[];
}

function groupBySector(holdings: Holding[]): SectorGroup[] {
  const total = holdings.reduce(
    (sum, h) => sum + (h.last_price || h.avg_cost) * h.quantity, 0
  );
  const map: Record<string, Holding[]> = {};
  for (const h of holdings) {
    const s = h.sector || "Unknown";
    if (!map[s]) map[s] = [];
    map[s].push(h);
  }
  return Object.entries(map)
    .map(([sector, hs]) => {
      const totalValue = hs.reduce(
        (sum, h) => sum + (h.last_price || h.avg_cost) * h.quantity, 0
      );
      return { sector, totalValue, pct: total > 0 ? (totalValue / total) * 100 : 0, holdings: hs };
    })
    .sort((a, b) => b.totalValue - a.totalValue);
}

const SECTOR_COLORS = [
  "#378ADD", "#1D9E75", "#EF9F27", "#E24B4A",
  "#9B59B6", "#E67E22", "#16A085", "#2C3E50",
];

interface Props {
  onConnectKite?: () => void;
  onStockSelect?: (ticker: string) => void;
}

export function PortfolioScreen({ onConnectKite, onStockSelect }: Props) {
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState<string | null>(null);
  const [sortBy, setSortBy]     = useState<"value" | "pnl" | "pnlPct">("value"); // used for sort pills

  useEffect(() => {
    portfolio.getHoldings()
      .then(data => { setHoldings(data ?? []); setLoading(false); })
      .catch(e  => { setError(e.message); setLoading(false); });
  }, []);

  const sectors = useMemo(() => groupBySector(holdings), [holdings]);

  const totalValue    = holdings.reduce((s, h) => s + (h.last_price || h.avg_cost) * h.quantity, 0);
  const totalCost     = holdings.reduce((s, h) => s + h.avg_cost * h.quantity, 0);
  const totalPnl      = totalValue - totalCost;
  const totalPnlPct   = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0;

  // Top gainers and losers
  const topGainers = useMemo(() =>
    [...holdings].sort((a, b) => (b.unrealized_pct ?? 0) - (a.unrealized_pct ?? 0)).slice(0, 3),
    [holdings]
  );
  const topLosers = useMemo(() =>
    [...holdings].sort((a, b) => (a.unrealized_pct ?? 0) - (b.unrealized_pct ?? 0)).slice(0, 3),
    [holdings]
  );

  if (loading) return <div className="screen-loading"><div className="spinner" /></div>;
  if (error)   return <div className="screen-error">Failed to load: {error}</div>;
  if (!holdings.length) return (
    <div className="screen">
      <div className="empty-state">
        <div className="empty-icon">📈</div>
        <h2>No holdings found</h2>
        <p>Connect your brokerage to sync your portfolio</p>
        <button className="btn-primary" onClick={onConnectKite}>Connect Kite</button>
      </div>
    </div>
  );

  return (
    <div className="screen">
      {/* Summary cards */}
      <div className="summary-row">
        <SummaryCard label="Portfolio Value"  value={`₹${totalValue.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`} />
        <SummaryCard
          label="Total P&L"
          value={`${totalPnl >= 0 ? "+" : ""}₹${Math.abs(totalPnl).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`}
          sub={`${totalPnlPct >= 0 ? "+" : ""}${totalPnlPct.toFixed(2)}%`}
          positive={totalPnl >= 0}
        />
        <SummaryCard label="Holdings" value={String(holdings.length)} />
        <SummaryCard label="Sectors"  value={String(sectors.length)} />
      </div>

      {/* Top movers */}
      <div className="movers-row">
        <div className="card mover-card">
          <h2 className="card-title">Top Gainers</h2>
          {topGainers.map(h => (
            <div key={h.ticker} className="mover-item" onClick={() => onStockSelect?.(h.ticker)}>
              <span className="mover-ticker">{h.ticker}</span>
              <span className="positive">+{(h.unrealized_pct ?? 0).toFixed(1)}%</span>
            </div>
          ))}
        </div>
        <div className="card mover-card">
          <h2 className="card-title">Top Losers</h2>
          {topLosers.filter(h => (h.unrealized_pct ?? 0) < 0).map(h => (
            <div key={h.ticker} className="mover-item" onClick={() => onStockSelect?.(h.ticker)}>
              <span className="mover-ticker">{h.ticker}</span>
              <span className="negative">{(h.unrealized_pct ?? 0).toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>

      {/* Sector allocation bar */}
      <div className="card">
        <h2 className="card-title">Sector Allocation</h2>
        <div className="allocation-bar">
          {sectors.map((s, i) => (
            <div
              key={s.sector}
              className="allocation-segment"
              style={{ width: `${s.pct}%`, background: SECTOR_COLORS[i % SECTOR_COLORS.length] }}
              title={`${s.sector}: ${s.pct.toFixed(1)}%`}
            />
          ))}
        </div>
        <div className="allocation-legend">
          {sectors.map((s, i) => (
            <div key={s.sector} className="legend-item">
              <span className="legend-dot" style={{ background: SECTOR_COLORS[i % SECTOR_COLORS.length] }} />
              <span className="legend-label">{s.sector}</span>
              <span className="legend-pct">{s.pct.toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>

      {/* Sort controls */}
      <div className="sort-controls">
        <span className="sort-label">Sort by:</span>
        <div className="filter-pills">
          <button className={`pill ${sortBy === "value" ? "active" : ""}`} onClick={() => setSortBy("value")}>Value</button>
          <button className={`pill ${sortBy === "pnl" ? "active" : ""}`} onClick={() => setSortBy("pnl")}>P&L</button>
          <button className={`pill ${sortBy === "pnlPct" ? "active" : ""}`} onClick={() => setSortBy("pnlPct")}>P&L %</button>
        </div>
      </div>

      {/* Holdings by sector */}
      {sectors.map(group => (
        <div key={group.sector} className="card">
          <div className="sector-header">
            <h2 className="card-title">{group.sector}</h2>
            <span className="sector-value">
              ₹{group.totalValue.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
              <span className="sector-pct"> · {group.pct.toFixed(1)}%</span>
            </span>
          </div>

          <div className="table-scroll"><table className="holdings-table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Qty</th>
                <th>Avg Cost</th>
                <th>Current</th>
                <th>P&L</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              {group.holdings.map(h => {
                const value  = (h.last_price || h.avg_cost) * h.quantity;
                const pnlPct = h.unrealized_pct ?? 0;
                const pnl    = h.unrealized_pnl ?? 0;
                return (
                  <tr
                    key={h.ticker}
                    className="holding-row"
                    onClick={() => onStockSelect?.(h.ticker)}
                  >
                    <td>
                      <div className="ticker-name">{h.ticker}</div>
                      <div className="company-name">{h.company_name}</div>
                    </td>
                    <td>{h.quantity}</td>
                    <td>₹{h.avg_cost.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</td>
                    <td>₹{(h.last_price || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 })}</td>
                    <td className={pnlPct >= 0 ? "positive" : "negative"}>
                      {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
                      <div className="pnl-abs">₹{Math.abs(pnl).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</div>
                    </td>
                    <td>₹{value.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</td>
                  </tr>
                );
              })}
            </tbody>
          </table></div>
        </div>
      ))}
    </div>
  );
}

function SummaryCard({
  label, value, sub, positive
}: { label: string; value: string; sub?: string; positive?: boolean }) {
  return (
    <div className="summary-card">
      <div className="summary-label">{label}</div>
      <div className={`summary-value ${positive === true ? "positive" : positive === false ? "negative" : ""}`}>
        {value}
      </div>
      {sub && <div className={`summary-sub ${positive === true ? "positive" : positive === false ? "negative" : ""}`}>{sub}</div>}
    </div>
  );
}
