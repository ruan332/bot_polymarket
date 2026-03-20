"use client";

import dynamic from "next/dynamic";
import { useEffect, useState, useRef } from "react";

type AgentStatus = {
  model: string;
  running: boolean;
  config_version: number;
  last_seen: string | null;
};

type CostSummary = {
  agent: string;
  cost_usd: number;
  calls: number;
  input_tokens: number;
  output_tokens: number;
};

type Signal = {
  signal_id: string;
  market_question: string;
  asset_symbol: string;
  crypto_tier: "btc" | "major" | "small_cap";
  direction: "YES" | "NO";
  edge: number;
  confidence: number;
  price: number;
  volume_24h: number;
  news_validation?: {
    validated: boolean;
    support_score: number;
    conflict_score: number;
    source_count: number;
    provider_used?: string;
    fallback_used?: boolean;
    primary_error_type?: string | null;
    reason: string;
  } | null;
  created_at: string;
};

type Decision = {
  signal_id: string;
  asset_symbol: string;
  crypto_tier: "btc" | "major" | "small_cap" | null;
  approved: boolean;
  corrected_price_limit: number | null;
  kelly_size: number;
  notes: string;
  created_at: string;
};

type Order = {
  order_id: string;
  signal_id: string;
  market_id: string;
  market_question?: string;
  asset_symbol: string;
  crypto_tier: "btc" | "major" | "small_cap" | null;
  direction: "YES" | "NO";
  size: number;
  price_limit: number;
  status: string;
  notional_usd: number;
  created_at: string;
};

type RiskEvent = {
  reason: string;
  agent: string;
  created_at: string;
};

type PortfolioSummary = {
  available_balance: number;
  total_exposure: number;
  current_market_value: number;
  total_equity: number;
  total_pnl: number;
  open_positions: number;
  realized_pnl: number;
  unrealized_pnl: number;
};

type Position = {
  market_id: string;
  market_question: string;
  asset_symbol?: string;
  crypto_tier?: "btc" | "major" | "small_cap";
  direction: "YES" | "NO";
  size: number;
  average_price: number;
  current_price: number;
  current_value_usd: number;
  unrealized_pnl: number;
};

type PerformanceReport = {
  generated_at: string;
  window_hours: number;
  asset_filter: string;
  tier_filter: string;
  summary: {
    signals: number;
    decisions: number;
    orders: number;
    risk_events: number;
    approval_rate: number;
    execution_rate: number;
    positive_position_rate: number;
    avg_edge: number;
    avg_confidence: number;
    total_order_notional: number;
    avg_order_notional: number;
    llm_cost_usd: number;
    available_balance: number;
    total_exposure: number;
    current_market_value: number;
    total_equity: number;
    total_pnl: number;
    open_positions: number;
    realized_pnl: number;
    unrealized_pnl: number;
  };
  cost_by_agent: Array<{ agent: string; cost_usd: number; calls: number }>;
  risk_breakdown: Array<{ label: string; count: number }>;
  asset_breakdown: Array<{ label: string; count: number }>;
  tier_breakdown: Array<{ label: string; count: number }>;
  news_breakdown: Array<{ label: string; count: number }>;
  news_provider_breakdown: Array<{ label: string; count: number }>;
  news_fallback_breakdown: Array<{ label: string; count: number }>;
  last_news_provider: {
    provider_used: string;
    fallback_used: boolean;
    signal_id: string;
    asset_symbol: string;
    crypto_tier: string;
    created_at: string;
  } | null;
  top_markets: Array<{
    market_id: string;
    market_question: string;
    asset_symbol: string;
    crypto_tier: string;
    signal_count: number;
    order_count: number;
    avg_edge: number;
    avg_confidence: number;
  }>;
  open_positions: Position[];
  time_series: {
    pipeline: Array<{
      bucket: string;
      signals: number;
      decisions: number;
      orders: number;
      risk_events: number;
    }>;
    equity: Array<{
      created_at: string;
      total_equity: number;
      total_pnl: number;
      unrealized_pnl: number;
      available_balance: number;
    }>;
  };
};

type LogEntry = {
  id: string;
  time: string;
  type: "signal" | "decision" | "order" | "risk";
  message: string;
  raw_time: string;
};

type DashboardState = {
  statuses: Record<string, AgentStatus>;
  costs: CostSummary[];
  signals: Signal[];
  decisions: Decision[];
  orders: Order[];
  riskEvents: RiskEvent[];
  portfolio: PortfolioSummary | null;
  positions: Position[];
  performance: PerformanceReport | null;
  logs: LogEntry[];
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api";
const OperationsCharts = dynamic(
  () => import("./charts").then((module) => module.OperationsCharts),
  { ssr: false }
);

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}`);
  }
  return response.json() as Promise<T>;
}

function asPercent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

function asCurrency(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function breakdownCount(items: Array<{ label: string; count: number }>, label: string) {
  return items.find((item) => item.label === label)?.count ?? 0;
}

export function DashboardClient() {
  const [state, setState] = useState<DashboardState>({
    statuses: {},
    costs: [],
    signals: [],
    decisions: [],
    orders: [],
    riskEvents: [],
    portfolio: null,
    positions: [],
    performance: null,
    logs: [],
  });
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string>("booting");

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const [statuses, costs, signals, decisions, orders, riskEvents, portfolio, positions, performance] =
          await Promise.all([
            getJson<Record<string, AgentStatus>>("/agents/status"),
            getJson<CostSummary[]>("/costs/daily"),
            getJson<Signal[]>("/signals/recent"),
            getJson<Decision[]>("/decisions/recent"),
            getJson<Order[]>("/orders/recent"),
            getJson<RiskEvent[]>("/risk-events/recent"),
            getJson<PortfolioSummary>("/portfolio/summary"),
            getJson<Position[]>("/portfolio/positions"),
            getJson<PerformanceReport>("/metrics/performance?hours=24"),
          ]);

        if (!active) {
          return;
        }

        // Generate unified logs from events
        const newLogs: LogEntry[] = [];
        
        signals.forEach(s => newLogs.push({
          id: `sig-${s.signal_id}`,
          time: new Date(s.created_at).toLocaleTimeString(),
          type: "signal",
          message: `NEW SIGNAL: ${s.asset_symbol} ${s.direction} | Edge: ${s.edge.toFixed(3)} | ${s.market_question}`,
          raw_time: s.created_at
        }));

        decisions.forEach(d => newLogs.push({
          id: `dec-${d.signal_id}-${d.created_at}`,
          time: new Date(d.created_at).toLocaleTimeString(),
          type: "decision",
          message: `DECISION: ${d.asset_symbol} ${d.approved ? "APPROVED" : "REJECTED"} | Kelly: ${d.kelly_size.toFixed(2)} | ${d.notes}`,
          raw_time: d.created_at
        }));

        orders.forEach(o => newLogs.push({
          id: `ord-${o.order_id}`,
          time: new Date(o.created_at).toLocaleTimeString(),
          type: "order",
          message: `ORDER: ${o.asset_symbol} ${o.direction} | ${o.size} shares @ ${o.price_limit.toFixed(3)} | Status: ${o.status}`,
          raw_time: o.created_at
        }));

        riskEvents.forEach(r => newLogs.push({
          id: `risk-${r.created_at}-${r.agent}`,
          time: new Date(r.created_at).toLocaleTimeString(),
          type: "risk",
          message: `RISK BLOCK: [${r.agent}] ${r.reason}`,
          raw_time: r.created_at
        }));

        newLogs.sort((a, b) => b.raw_time.localeCompare(a.raw_time));

        setState({
          statuses,
          costs,
          signals,
          decisions,
          orders,
          riskEvents,
          portfolio,
          positions,
          performance,
          logs: newLogs.slice(0, 100), // Keep last 100 logs
        });
        setError(null);
        setLastUpdated(new Date().toLocaleTimeString());
      } catch (loadError) {
        if (!active) {
          return;
        }
        setError(loadError instanceof Error ? loadError.message : "Unknown error");
      }
    }

    void load();
    const interval = window.setInterval(() => {
      void load();
    }, 5000);

    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const performance = state.performance;
  const summary = performance?.summary;
  const lastNewsProvider = performance?.last_news_provider;
  const fallbackHits = breakdownCount(performance?.news_fallback_breakdown ?? [], "fallback_used");
  
  const runningAgents = Object.values(state.statuses).filter(s => s.running).length;
  const totalAgents = Object.keys(state.statuses).length;
  const lastActivity = state.logs[0]?.time ?? "none";

  return (
    <main className="terminal-shell">
      <section className="hero-panel">
        <div className="terminal-strip">
          <span className="terminal-pill">paper trading</span>
          <span className="terminal-pill">24h report</span>
          <span className="terminal-pill">redis streams</span>
          <span className="terminal-pill">updated {lastUpdated}</span>
          {error ? <span className="terminal-pill danger">api error: {error}</span> : null}
        </div>

        <div className="hero-copy">
          <div>
            <p className="eyebrow">$ sudo poly-console --tail=live</p>
            <h1>System Command Center</h1>
            <p className="hero-text">
              Multi-agent autonomous intelligence suite for Polymarket. 
              Real-time synchronization with Redis cluster and predictive modeling.
            </p>
          </div>

          <div className="hero-console">
            <div className="console-line">
              <span className="prompt">root@polymarket:~$</span>
              <span>uptime --status</span>
            </div>
            <div className="console-line">
              <span className="prompt">&gt;</span>
              <span>Active Agents: {runningAgents}/{totalAgents}</span>
            </div>
            <div className="console-line">
              <span className="prompt">&gt;</span>
              <span>Last Pulse: {lastActivity}</span>
            </div>
            <div className="console-line">
              <span className="prompt">&gt;</span>
              <span>Approval: {summary ? asPercent(summary.approval_rate) : "0.0%"}</span>
            </div>
          </div>
        </div>
      </section>

      <section className="metrics-grid">
        <MetricCard label="Net Equity" value={asCurrency(state.portfolio?.total_equity ?? 0)} hint="Current liquidation value" tone="cyan" />
        <MetricCard label="Liquidity" value={asCurrency(state.portfolio?.available_balance ?? 0)} hint="Ready for deployment" tone="green" />
        <MetricCard label="Exposure" value={asCurrency(state.portfolio?.total_exposure ?? 0)} hint="Capital at risk" tone="amber" />
        <MetricCard label="Positions" value={`${state.portfolio?.open_positions ?? 0}`} hint="Active market entries" tone="slate" />
        <MetricCard label="Win Rate" value={summary ? asPercent(summary.approval_rate) : "0.0%"} hint="System approval confidence" tone="cyan" />
        <MetricCard label="Throughput" value={summary ? asPercent(summary.execution_rate) : "0.0%"} hint="Approval to execution ratio" tone="amber" />
        <MetricCard label="Avg Alpha" value={summary ? summary.avg_edge.toFixed(3) : "0.000"} hint="Market edge per signal" tone="green" />
        <MetricCard label="API Overhead" value={asCurrency(summary?.llm_cost_usd ?? 0)} hint="LLM provider expenditure" tone="red" />
        <MetricCard label="Active Agents" value={`${runningAgents}/${totalAgents}`} hint="Core runtime status" tone="green" />
        <MetricCard label="Last Signal" value={lastActivity} hint="Most recent market event" tone="slate" />
      </section>

      <div className="dashboard-grid">
        <article className="panel span-4">
          <div className="panel-head">
            <h3>Log Terminal</h3>
            <span className="terminal-pill">live feed</span>
          </div>
          <LogTerminal logs={state.logs} />
        </article>

        <article className="panel span-3">
          <div className="panel-head">
            <h3>Agent Network</h3>
            <span className="terminal-pill">{runningAgents} online</span>
          </div>
          <div className="stack-list">
            {Object.entries(state.statuses).map(([name, status]) => (
              <div key={name} className="stack-row">
                <div className="row-main agent-status">
                  <div className={`status-indicator ${status.running ? "online" : "offline"}`}></div>
                  <strong>{name}</strong>
                  <span className={status.running ? "running mono" : "blocked mono"}>
                    {status.running ? "ACTIVE" : "IDLE"}
                  </span>
                </div>
                <span className="mono subtle">{status.model}</span>
                <span className="mono subtle">v{status.config_version} | seen {status.last_seen ? new Date(status.last_seen).toLocaleTimeString() : "never"}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel span-5">
           <OperationsCharts
            costs={performance?.cost_by_agent ?? state.costs}
            pipeline={performance?.time_series.pipeline ?? []}
            equity={performance?.time_series.equity ?? []}
            riskBreakdown={performance?.risk_breakdown ?? []}
          />
        </article>

        <article className="panel span-8">
          <div className="panel-head">
            <h3>Portfolio Positions</h3>
            <span className="terminal-pill">{state.positions.length} active</span>
          </div>
          <div className="stack-list">
            {state.positions.length === 0 ? (
              <p className="mono subtle p-4">SYSTEM READY. SCANNING FOR ENTRIES...</p>
            ) : (
              state.positions.map((position) => (
                <div key={`${position.market_id}-${position.direction}`} className="stack-row">
                  <div className="row-main">
                    <strong>{position.asset_symbol ?? "?"} {position.direction} <span className="mono subtle">{position.market_question || position.market_id}</span></strong>
                    <span className={position.unrealized_pnl >= 0 ? "running mono" : "blocked mono"}>
                      {asCurrency(position.unrealized_pnl)}
                    </span>
                  </div>
                  <div className="row-meta">
                    <span className="mono">{position.size} shares</span>
                    <span className="mono">ENTRY: {position.average_price.toFixed(3)}</span>
                    <span className="mono">MARK: {position.current_price.toFixed(3)}</span>
                    <span className="mono">VALUE: {asCurrency(position.current_value_usd)}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </article>

        <article className="panel span-4">
          <div className="panel-head">
            <h3>Risk Monitoring</h3>
            <span className="terminal-pill">watchdog</span>
          </div>
          <div className="stack-list compact">
            {state.riskEvents.slice(0, 10).map((event) => (
              <div key={`${event.agent}-${event.created_at}-${event.reason}`} className="stack-row">
                <div className="row-main">
                  <strong>{event.agent}</strong>
                  <span className="blocked mono">HALTED</span>
                </div>
                <span className="mono muted">{event.reason}</span>
                <span className="mono subtle text-right">{new Date(event.created_at).toLocaleTimeString()}</span>
              </div>
            ))}
            {state.riskEvents.length === 0 && <p className="mono subtle">NO CRITICAL ALERTS</p>}
          </div>
        </article>
      </div>
    </main>
  );
}

function LogTerminal({ logs }: { logs: LogEntry[] }) {
  const terminalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = 0; // Newest at top for easier reading in small panels
    }
  }, [logs]);

  return (
    <div className="log-terminal" ref={terminalRef}>
      {logs.map((log) => (
        <div key={log.id} className="log-entry">
          <span className="log-time">[{log.time}]</span>
          <span className={`log-tag tag-${log.type}`}>{log.type}</span>
          <span className="log-message">{log.message}</span>
        </div>
      ))}
      {logs.length === 0 && <div className="log-entry subtle">Waiting for system logs...</div>}
    </div>
  );
}

function MetricCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  tone: "green" | "cyan" | "amber" | "red" | "slate";
}) {
  return (
    <article className={`metric-card tone-${tone}`}>
      <span className="metric-label">{label}</span>
      <strong className="metric-value">{value}</strong>
      <span className="metric-hint">{hint}</span>
    </article>
  );
}

