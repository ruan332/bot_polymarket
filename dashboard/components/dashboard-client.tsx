"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

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
  direction: "YES" | "NO";
  edge: number;
  confidence: number;
  price: number;
  created_at: string;
};

type Decision = {
  signal_id: string;
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
  risk_breakdown: Array<{ reason: string; count: number }>;
  top_markets: Array<{
    market_id: string;
    market_question: string;
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
            <p className="eyebrow">$ boot polymarket-bot --mode paper</p>
            <h1>Terminal-grade market room.</h1>
            <p className="hero-text">
              Dark operations console for scanning, review, execution and risk control. The board tracks the live
              paper-trading loop, performance over the last 24 hours and current exposure with a compact mobile-first
              layout.
            </p>
          </div>

          <div className="hero-console">
            <div className="console-line">
              <span className="prompt">$</span>
              <span>window: {performance?.window_hours ?? 24}h</span>
            </div>
            <div className="console-line">
              <span className="prompt">$</span>
              <span>signals: {summary?.signals ?? 0}</span>
            </div>
            <div className="console-line">
              <span className="prompt">$</span>
              <span>approval: {summary ? asPercent(summary.approval_rate) : "0.0%"}</span>
            </div>
            <div className="console-line">
              <span className="prompt">$</span>
              <span>execution: {summary ? asPercent(summary.execution_rate) : "0.0%"}</span>
            </div>
          </div>
        </div>
      </section>

      <section className="metrics-grid">
        <MetricCard label="Total Equity" value={asCurrency(state.portfolio?.total_equity ?? 0)} hint="cash plus marked positions" tone="cyan" />
        <MetricCard label="Available Balance" value={asCurrency(state.portfolio?.available_balance ?? 0)} hint="free bankroll remaining" tone="green" />
        <MetricCard label="Total Exposure" value={asCurrency(state.portfolio?.total_exposure ?? 0)} hint="paper notional currently deployed" tone="amber" />
        <MetricCard label="Open Positions" value={`${state.portfolio?.open_positions ?? 0}`} hint="book entries still active" tone="slate" />
        <MetricCard label="Approval Rate" value={summary ? asPercent(summary.approval_rate) : "0.0%"} hint="reviews approved over emitted signals" tone="cyan" />
        <MetricCard label="Execution Rate" value={summary ? asPercent(summary.execution_rate) : "0.0%"} hint="paper orders over approved reviews" tone="amber" />
        <MetricCard label="Avg Edge" value={summary ? summary.avg_edge.toFixed(3) : "0.000"} hint="average signal edge in window" tone="green" />
        <MetricCard label="24h LLM Cost" value={asCurrency(summary?.llm_cost_usd ?? 0)} hint="provider spend in the active window" tone="red" />
      </section>

      <section className="dashboard-grid">
        <article className="panel panel-sidebar span-3">
          <div className="panel-head">
            <h3>Agent Runtime</h3>
            <span className="terminal-pill">{Object.keys(state.statuses).length} agents</span>
          </div>
          <div className="stack-list">
            {Object.entries(state.statuses).map(([name, status]) => (
              <div key={name} className="stack-row">
                <div className="row-main">
                  <strong>{name}</strong>
                  <span className={status.running ? "running mono" : "blocked mono"}>
                    {status.running ? "online" : "offline"}
                  </span>
                </div>
                <span className="mono muted">{status.model}</span>
                <span className="mono subtle">cfg #{status.config_version}</span>
                <span className="mono subtle">
                  seen {status.last_seen ? new Date(status.last_seen).toLocaleTimeString() : "never"}
                </span>
              </div>
            ))}
          </div>
        </article>

        <OperationsCharts
          costs={performance?.cost_by_agent ?? state.costs}
          pipeline={performance?.time_series.pipeline ?? []}
          equity={performance?.time_series.equity ?? []}
          riskBreakdown={performance?.risk_breakdown ?? []}
        />

        <article className="panel span-5">
          <div className="panel-head">
            <h3>Top Markets</h3>
            <span className="terminal-pill">signal density</span>
          </div>
          <div className="stack-list">
            {(performance?.top_markets ?? []).slice(0, 6).map((market) => (
              <div key={market.market_id} className="stack-row">
                <div className="row-main">
                  <strong>{market.market_question}</strong>
                  <span className="mono subtle">{market.signal_count} signals</span>
                </div>
                <div className="row-meta">
                  <span className="mono">edge {market.avg_edge.toFixed(3)}</span>
                  <span className="mono">conf {market.avg_confidence.toFixed(2)}</span>
                  <span className="mono">orders {market.order_count}</span>
                </div>
              </div>
            ))}
          </div>
        </article>

        <article className="panel span-4">
          <div className="panel-head">
            <h3>Recent Reviews</h3>
            <span className="terminal-pill">{state.decisions.length} cached</span>
          </div>
          <div className="stack-list compact">
            {state.decisions.slice(0, 6).map((decision) => (
              <div key={`${decision.signal_id}-${decision.created_at}`} className="stack-row">
                <div className="row-main">
                  <strong>{decision.approved ? "approved" : "rejected"}</strong>
                  <span className={decision.approved ? "running mono" : "blocked mono"}>
                    kelly {decision.kelly_size}
                  </span>
                </div>
                <span>{decision.notes}</span>
                <span className="mono subtle">{new Date(decision.created_at).toLocaleString()}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel span-8">
          <div className="panel-head">
            <h3>Open Positions</h3>
            <span className="terminal-pill">{state.positions.length} active</span>
          </div>
          <div className="stack-list">
            {state.positions.slice(0, 8).map((position) => (
              <div key={`${position.market_id}-${position.direction}`} className="stack-row">
                <div className="row-main">
                  <strong>{position.direction} {position.market_question || position.market_id}</strong>
                  <span className={position.unrealized_pnl >= 0 ? "running mono" : "blocked mono"}>
                    {asCurrency(position.unrealized_pnl)}
                  </span>
                </div>
                <div className="row-meta">
                  <span className="mono">{position.size} contracts</span>
                  <span className="mono">avg {position.average_price.toFixed(3)}</span>
                  <span className="mono">mark {position.current_price.toFixed(3)}</span>
                  <span className="mono">value {asCurrency(position.current_value_usd)}</span>
                </div>
              </div>
            ))}
          </div>
        </article>

        <article className="panel span-4">
          <div className="panel-head">
            <h3>Recent Orders</h3>
            <span className="terminal-pill">{state.orders.length} cached</span>
          </div>
          <div className="stack-list compact">
            {state.orders.slice(0, 6).map((order) => (
              <div key={order.order_id} className="stack-row">
                <div className="row-main">
                  <strong>{order.direction}</strong>
                  <span className={order.status === "simulated" ? "running mono" : "blocked mono"}>
                    {order.status}
                  </span>
                </div>
                <span>{order.market_question || order.market_id}</span>
                <div className="row-meta">
                  <span className="mono">{order.size} @ {order.price_limit.toFixed(3)}</span>
                  <span className="mono">{asCurrency(order.notional_usd)}</span>
                </div>
              </div>
            ))}
          </div>
        </article>

        <article className="panel span-6">
          <div className="panel-head">
            <h3>Recent Signals</h3>
            <span className="terminal-pill">{state.signals.length} cached</span>
          </div>
          <div className="stack-list">
            {state.signals.slice(0, 8).map((signal) => (
              <div key={signal.signal_id} className="stack-row">
                <div className="row-main">
                  <strong>{signal.direction} {signal.market_question}</strong>
                  <span className="mono subtle">{signal.edge.toFixed(3)} edge</span>
                </div>
                <div className="row-meta">
                  <span className="mono">price {signal.price.toFixed(3)}</span>
                  <span className="mono">conf {signal.confidence.toFixed(2)}</span>
                  <span className="mono">{new Date(signal.created_at).toLocaleTimeString()}</span>
                </div>
              </div>
            ))}
          </div>
        </article>

        <article className="panel span-6">
          <div className="panel-head">
            <h3>Risk Tape</h3>
            <span className="terminal-pill">{state.riskEvents.length} cached</span>
          </div>
          <div className="stack-list">
            {state.riskEvents.slice(0, 8).map((event) => (
              <div key={`${event.agent}-${event.created_at}-${event.reason}`} className="stack-row">
                <div className="row-main">
                  <strong>{event.agent}</strong>
                  <span className="blocked mono">blocked</span>
                </div>
                <span>{event.reason}</span>
                <span className="mono subtle">{new Date(event.created_at).toLocaleString()}</span>
              </div>
            ))}
          </div>
        </article>
      </section>
    </main>
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
