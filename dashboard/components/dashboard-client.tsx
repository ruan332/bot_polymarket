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
  created_at: string;
};

type Order = {
  order_id: string;
  signal_id: string;
  market_id: string;
  direction: "YES" | "NO";
  size: number;
  price_limit: number;
  status: string;
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
  open_positions: number;
  realized_pnl: number;
  unrealized_pnl: number;
};

type MetricsOverview = {
  signals: number;
  decisions: number;
  orders: number;
  risk_events: number;
  portfolio: PortfolioSummary;
};

type DashboardState = {
  statuses: Record<string, AgentStatus>;
  costs: CostSummary[];
  signals: Signal[];
  orders: Order[];
  riskEvents: RiskEvent[];
  metrics: MetricsOverview | null;
  portfolio: PortfolioSummary | null;
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

export function DashboardClient() {
  const [state, setState] = useState<DashboardState>({
    statuses: {},
    costs: [],
    signals: [],
    orders: [],
    riskEvents: [],
    metrics: null,
    portfolio: null,
  });
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string>("waiting");

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const [statuses, costs, signals, orders, riskEvents, metrics, portfolio] = await Promise.all([
          getJson<Record<string, AgentStatus>>("/agents/status"),
          getJson<CostSummary[]>("/costs/daily"),
          getJson<Signal[]>("/signals/recent"),
          getJson<Order[]>("/orders/recent"),
          getJson<RiskEvent[]>("/risk-events/recent"),
          getJson<MetricsOverview>("/metrics/overview"),
          getJson<PortfolioSummary>("/portfolio/summary"),
        ]);
        if (!active) {
          return;
        }
        setState({ statuses, costs, signals, orders, riskEvents, metrics, portfolio });
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

  const throughput = [
    { label: "Signals", value: state.metrics?.signals ?? 0 },
    { label: "Decisions", value: state.metrics?.decisions ?? 0 },
    { label: "Orders", value: state.metrics?.orders ?? 0 },
    { label: "Risk", value: state.metrics?.risk_events ?? 0 },
  ];

  return (
    <main className="shell">
      <section className="hero">
        <div className="hero-strip">
          <div className="chip">paper trading</div>
          <div className="chip">redis streams</div>
          <div className="chip">hot reload via yaml + redis</div>
          <div className="chip">updated {lastUpdated}</div>
        </div>
        <h1>Polymarket bot room.</h1>
        <p>
          Internal operations view for the scanner, reviewer and executor pipeline. The board polls the FastAPI
          service every five seconds and surfaces the paper-trading path end to end.
        </p>
        {error ? <div className="chip">api error: {error}</div> : null}
      </section>

      <section className="grid">
        <article className="panel span-3">
          <h2>Available Balance</h2>
          <div className="metric">
            <strong>${state.portfolio?.available_balance.toFixed(2) ?? "0.00"}</strong>
            <span>paper bankroll remaining</span>
          </div>
        </article>
        <article className="panel span-3">
          <h2>Total Exposure</h2>
          <div className="metric">
            <strong>${state.portfolio?.total_exposure.toFixed(2) ?? "0.00"}</strong>
            <span>current notional at risk</span>
          </div>
        </article>
        <article className="panel span-3">
          <h2>Open Positions</h2>
          <div className="metric">
            <strong>{state.portfolio?.open_positions ?? 0}</strong>
            <span>paper positions on the book</span>
          </div>
        </article>
        <article className="panel span-3">
          <h2>Pipeline Events</h2>
          <div className="metric">
            <strong>{state.metrics?.signals ?? 0}</strong>
            <span>signals emitted so far</span>
          </div>
        </article>

        <article className="panel span-4">
          <h3>Agents</h3>
          <div className="status-list">
            {Object.entries(state.statuses).map(([name, status]) => (
              <div key={name} className="status-row">
                <div className="status-top">
                  <strong>{name}</strong>
                  <span className={status.running ? "running mono" : "stopped mono"}>
                    {status.running ? "running" : "stopped"}
                  </span>
                </div>
                <span className="mono muted">{status.model}</span>
                <span className="mono muted">cfg #{status.config_version}</span>
              </div>
            ))}
          </div>
        </article>

        <OperationsCharts costs={state.costs} throughput={throughput} />

        <article className="panel span-6">
          <h3>Recent Signals</h3>
          <div className="event-list">
            {state.signals.slice(0, 5).map((signal) => (
              <div key={signal.signal_id} className="event-row">
                <div className="event-top">
                  <strong>{signal.direction}</strong>
                  <span className="mono">{signal.edge.toFixed(3)} edge</span>
                </div>
                <span>{signal.market_question}</span>
                <span className="mono muted">confidence {signal.confidence.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel span-6">
          <h3>Paper Orders</h3>
          <div className="event-list">
            {state.orders.slice(0, 5).map((order) => (
              <div key={order.order_id} className="event-row">
                <div className="event-top">
                  <strong>{order.direction}</strong>
                  <span className={order.status === "simulated" ? "approved mono" : "blocked mono"}>
                    {order.status}
                  </span>
                </div>
                <span className="mono">
                  {order.size} @ {order.price_limit.toFixed(3)}
                </span>
                <span className="mono muted">{order.market_id}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel span-6">
          <h3>Risk Events</h3>
          <div className="event-list">
            {state.riskEvents.slice(0, 5).map((event) => (
              <div key={`${event.agent}-${event.created_at}-${event.reason}`} className="event-row">
                <div className="event-top">
                  <strong>{event.agent}</strong>
                  <span className="blocked mono">blocked</span>
                </div>
                <span>{event.reason}</span>
                <span className="mono muted">{new Date(event.created_at).toLocaleString()}</span>
              </div>
            ))}
          </div>
        </article>
      </section>
    </main>
  );
}
