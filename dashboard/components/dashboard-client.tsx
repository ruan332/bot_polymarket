"use client";

import { useEffect, useRef, useState } from "react";

type AgentStatus = {
  running: boolean;
};

type Signal = {
  signal_id: string;
  asset_symbol: string;
  strategy_id: string;
  regime?: string;
  edge?: number;
  confidence?: number;
  direction?: "YES" | "NO";
  created_at: string;
};

type Decision = {
  signal_id: string;
  asset_symbol: string;
  approved: boolean;
  notes: string;
  kelly_size?: number;
  risk_fraction?: number;
  created_at: string;
};

type Order = {
  order_id: string;
  asset_symbol: string;
  strategy_id?: string;
  direction: "YES" | "NO";
  action?: "entry" | "scale_in" | "scale_out" | "close";
  realized_pnl_usd?: number;
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
  total_equity: number;
  total_pnl: number;
  realized_pnl: number;
  unrealized_pnl: number;
  open_positions: number;
};

type PerformanceSummary = {
  signals: number;
  decisions: number;
  orders: number;
  risk_events: number;
  approval_rate: number;
  execution_rate: number;
  win_rate?: number;
  total_order_notional: number;
  realized_pnl_window?: number;
  max_drawdown?: number;
  avg_edge: number;
  avg_confidence: number;
};

type PerformanceReport = {
  summary: PerformanceSummary;
  strategy_breakdown?: Array<{ label: string; signals: number; orders: number; realized_pnl_usd: number }>;
};

type MetricsOverview = {
  flow_summary?: {
    reviewer_approved: number;
    reviewer_rejected: number;
    executor_executed: number;
    executor_blocked: number;
    risk_passed: number;
    risk_blocked: number;
    pre_risk_blocked: number;
  };
  latest_scan_telemetry?: { risk_block_reasons?: Record<string, number>; pre_risk_block_reasons?: Record<string, number> } | null;
  latest_review_telemetry?: { reviewed_assets?: string[] } | null;
  latest_execution_telemetry?: { reviewed_assets?: string[]; exit_actions?: string[] } | null;
};

type LogLine = {
  time: string;
  text: string;
};

type StrategyGoalCheck = {
  label: string;
  value: string;
  ok: boolean;
};

type StrategyGoalStatus = {
  strategy: "pair_15m" | "momentum_15m";
  go: boolean;
  score: string;
  checks: StrategyGoalCheck[];
};

type DashboardState = {
  statuses: Record<string, AgentStatus>;
  portfolio: PortfolioSummary | null;
  overview: MetricsOverview | null;
  performance: PerformanceReport | null;
  pairPerformance: PerformanceReport | null;
  momentumPerformance: PerformanceReport | null;
  signals: Signal[];
  decisions: Decision[];
  orders: Order[];
  riskEvents: RiskEvent[];
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api";

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

function asCurrencySigned(value: number) {
  return `${value >= 0 ? "+" : ""}${asCurrency(value)}`;
}

function numberOr(value: unknown, fallback = 0) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function labelStrategy(value?: string) {
  if (!value) return "unknown";
  return value.replaceAll("_", " ");
}

function signalDirection(signal: Signal) {
  return signal.direction ?? "—";
}

function signalLog(signal: Signal) {
  const edge = numberOr(signal.edge);
  const confidence = numberOr(signal.confidence);
  return `${signal.asset_symbol} ${signalDirection(signal)} | ${labelStrategy(signal.strategy_id)} | edge ${edge.toFixed(3)} | conf ${asPercent(confidence)}`;
}

function buildStrategyLog(
  strategy: "pair_15m" | "momentum_15m",
  signals: Signal[],
  decisions: Decision[],
  orders: Order[],
  riskEvents: RiskEvent[],
) {
  const lines: Array<{ time: string; text: string }> = [];

  signals
    .filter((signal) => signal.strategy_id === strategy)
    .slice(0, 3)
    .forEach((signal) => lines.push({ time: signal.created_at, text: `SIG | ${signalLog(signal)}` }));

  decisions
    .filter((decision) => signals.some((signal) => signal.signal_id === decision.signal_id && signal.strategy_id === strategy))
    .slice(0, 2)
    .forEach((decision) =>
      lines.push({
        time: decision.created_at,
        text: `DEC | ${decision.asset_symbol} ${decision.approved ? "APPROVED" : "REJECTED"} | ${decision.notes}`,
      }),
    );

  orders
    .filter((order) => order.strategy_id === strategy)
    .slice(0, 2)
    .forEach((order) =>
      lines.push({
        time: order.created_at,
        text: `ORD | ${order.asset_symbol} ${order.direction} ${order.action ?? "entry"} | ${asCurrencySigned(order.realized_pnl_usd ?? 0)}`,
      }),
    );

  riskEvents
    .slice(0, 2)
    .forEach((event) =>
      lines.push({
        time: event.created_at,
        text: `RSK | [${event.agent}] ${event.reason}`,
      }),
    );

  return lines.sort((a, b) => b.time.localeCompare(a.time)).slice(0, 6);
}

function evaluateStrategyGoals(strategy: "pair_15m" | "momentum_15m", report: PerformanceReport | null): StrategyGoalStatus {
  const summary = report?.summary;
  const signals = numberOr(summary?.signals);
  const orders = numberOr(summary?.orders);
  const realized = numberOr(summary?.realized_pnl_window);
  const notional = numberOr(summary?.total_order_notional);
  const winRate = numberOr(summary?.win_rate);
  const maxDrawdown = numberOr(summary?.max_drawdown);
  const riskEvents = numberOr(summary?.risk_events);
  const riskLimit = strategy === "momentum_15m" ? 40 : 20;
  const riskPerSignal = signals > 0 ? riskEvents / signals : Number.POSITIVE_INFINITY;
  const pnlPerNotional = notional > 0 ? realized / notional : 0;

  const checks: StrategyGoalCheck[] = [
    { label: "PnL/Notional", value: asPercent(pnlPerNotional), ok: pnlPerNotional >= 0.01 },
    { label: "Win%", value: asPercent(winRate), ok: winRate >= 0.48 },
    { label: "Max DD", value: asPercent(maxDrawdown), ok: maxDrawdown <= 0.03 },
    {
      label: "Risk/Signal",
      value: Number.isFinite(riskPerSignal) ? riskPerSignal.toFixed(1) : "—",
      ok: Number.isFinite(riskPerSignal) && riskPerSignal < riskLimit,
    },
  ];

  const passed = checks.filter((check) => check.ok).length;
  return {
    strategy,
    go: passed === checks.length,
    score: `${passed}/${checks.length}`,
    checks,
  };
}

export function DashboardClient() {
  const [state, setState] = useState<DashboardState>({
    statuses: {},
    portfolio: null,
    overview: null,
    performance: null,
    pairPerformance: null,
    momentumPerformance: null,
    signals: [],
    decisions: [],
    orders: [],
    riskEvents: [],
  });
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState("booting");
  const [clock, setClock] = useState("--:--:--");
  const stateRef = useRef(state);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    setClock(new Date().toLocaleTimeString());
    const interval = window.setInterval(() => setClock(new Date().toLocaleTimeString()), 1000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    let active = true;

    async function load() {
      const current = stateRef.current;
      const requests = [
        { key: "statuses", path: "/agents/status", fallback: current.statuses },
        { key: "portfolio", path: "/portfolio/summary", fallback: current.portfolio },
        { key: "overview", path: "/metrics/overview", fallback: current.overview },
        { key: "performance", path: "/metrics/performance?hours=24", fallback: current.performance },
        { key: "pairPerformance", path: "/metrics/performance?hours=336&strategy=pair_15m", fallback: current.pairPerformance },
        { key: "momentumPerformance", path: "/metrics/performance?hours=336&strategy=momentum_15m", fallback: current.momentumPerformance },
        { key: "signals", path: "/signals/recent", fallback: current.signals },
        { key: "decisions", path: "/decisions/recent", fallback: current.decisions },
        { key: "orders", path: "/orders/recent", fallback: current.orders },
        { key: "riskEvents", path: "/risk-events/recent", fallback: current.riskEvents },
      ] as const;

      const results = await Promise.allSettled(requests.map((request) => getJson(request.path)));
      if (!active) return;

      const next = new Map<string, unknown>();
      const failures: string[] = [];
      results.forEach((result, index) => {
        const request = requests[index];
        if (result.status === "fulfilled") {
          next.set(request.key, result.value);
        } else {
          next.set(request.key, request.fallback);
          failures.push(request.path);
        }
      });

      try {
        setState({
          statuses: (next.get("statuses") ?? {}) as Record<string, AgentStatus>,
          portfolio: (next.get("portfolio") ?? null) as PortfolioSummary | null,
          overview: (next.get("overview") ?? null) as MetricsOverview | null,
          performance: (next.get("performance") ?? null) as PerformanceReport | null,
          pairPerformance: (next.get("pairPerformance") ?? null) as PerformanceReport | null,
          momentumPerformance: (next.get("momentumPerformance") ?? null) as PerformanceReport | null,
          signals: (next.get("signals") ?? []) as Signal[],
          decisions: (next.get("decisions") ?? []) as Decision[],
          orders: (next.get("orders") ?? []) as Order[],
          riskEvents: (next.get("riskEvents") ?? []) as RiskEvent[],
        });
        setError(failures.length > 0 ? `Failed: ${failures.join(", ")}` : null);
        setLastUpdated(new Date().toLocaleTimeString());
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : "Unknown error");
      }
    }

    void load();
    const interval = window.setInterval(() => void load(), 5000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const summary = state.performance?.summary;
  const balance = state.portfolio?.available_balance ?? 0;
  const pnl = state.portfolio?.total_pnl ?? 0;
  const realized = state.portfolio?.realized_pnl ?? 0;
  const unrealized = state.portfolio?.unrealized_pnl ?? 0;
  const equity = state.portfolio?.total_equity ?? 0;
  const runningAgents = Object.values(state.statuses).filter((status) => status.running).length;
  const totalAgents = Object.keys(state.statuses).length;
  const pairGoal = evaluateStrategyGoals("pair_15m", state.pairPerformance);
  const momentumGoal = evaluateStrategyGoals("momentum_15m", state.momentumPerformance);
  const pairLog = buildStrategyLog("pair_15m", state.signals, state.decisions, state.orders, state.riskEvents);
  const momentumLog = buildStrategyLog("momentum_15m", state.signals, state.decisions, state.orders, state.riskEvents);

  return (
    <main className="min-h-screen p-4 md:p-6">
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        <section className="border border-poly-border bg-poly-black p-4 md:p-5">
          <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-poly-dim">POLYTERM_V1.04</div>
              <div className="mt-2 flex items-center gap-2 font-mono text-sm">
                <span className={`h-2 w-2 rounded-full ${runningAgents > 0 ? "bg-poly-green animate-pulse-dot" : "bg-poly-red"}`} />
                <span className={runningAgents > 0 ? "text-poly-green" : "text-poly-red"}>
                  {runningAgents > 0 ? "ONLINE" : "DEGRADED"}
                </span>
              </div>
              <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                <Metric label="Balance" value={asCurrency(balance)} tone="text-poly-green" />
                <Metric label="PnL" value={asCurrencySigned(pnl)} tone={pnl >= 0 ? "text-poly-green" : "text-poly-red"} />
                <Metric label="Realized" value={asCurrencySigned(realized)} tone={realized >= 0 ? "text-poly-cyan" : "text-poly-red"} />
                <Metric label="Unrealized" value={asCurrencySigned(unrealized)} tone={unrealized >= 0 ? "text-poly-amber" : "text-poly-red"} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 text-right font-mono text-[10px] uppercase text-poly-dim md:min-w-[300px]">
              <div className="border border-poly-border px-3 py-2">
                <div>Win%</div>
                <div className="text-poly-cyan text-sm normal-case">{summary?.win_rate != null ? asPercent(summary.win_rate) : "—"}</div>
              </div>
              <div className="border border-poly-border px-3 py-2">
                <div>Equity</div>
                <div className="text-poly-cyan text-sm normal-case">{asCurrency(equity)}</div>
              </div>
              <div className="border border-poly-border px-3 py-2">
                <div>Agents</div>
                <div className="text-poly-cyan text-sm normal-case">{runningAgents}/{totalAgents}</div>
              </div>
              <div className="border border-poly-border px-3 py-2">
                <div>Updated</div>
                <div className="text-poly-cyan text-sm normal-case">{error ? "API_ERROR" : lastUpdated}</div>
              </div>
            </div>
          </div>
        </section>

        <section className="grid gap-4 lg:grid-cols-2">
          <StrategyGoalCard goal={pairGoal} />
          <StrategyGoalCard goal={momentumGoal} />
        </section>

        <section className="grid gap-4 lg:grid-cols-2">
          <LogPanel title="Pair_15m_Log" items={pairLog} />
          <LogPanel title="Momentum_15m_Log" items={momentumLog} />
        </section>

        <section className="border border-poly-border bg-poly-black p-4 font-mono text-[10px] text-poly-dim">
          <div className="flex flex-wrap gap-4">
            <span>Orders: <span className="text-poly-cyan">{summary?.orders ?? 0}</span></span>
            <span>Signals: <span className="text-poly-green">{summary?.signals ?? 0}</span></span>
            <span>Risk: <span className="text-poly-red">{summary?.risk_events ?? 0}</span></span>
            <span>Open: <span className="text-poly-amber">{state.portfolio?.open_positions ?? 0}</span></span>
            <span>Risk/Signal: <span className="text-poly-cyan">{summary && summary.signals > 0 ? (summary.risk_events / summary.signals).toFixed(1) : "—"}</span></span>
          </div>
        </section>
      </div>

      <div className="pointer-events-none fixed inset-0 crt-overlay" />
      <div className="fixed bottom-0 left-0 right-0 border-t border-poly-border bg-poly-surface-dim/90 px-4 py-1 font-mono text-[9px] text-poly-dim">
        <div className="mx-auto flex max-w-6xl items-center justify-between">
          <span>GO focus: balance, pnl, win%, strategy gates</span>
          <span>{clock}</span>
        </div>
      </div>
    </main>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className="border border-poly-border px-3 py-2">
      <div className="font-mono text-[8px] uppercase text-poly-dim">{label}</div>
      <div className={`mt-1 font-mono text-sm font-bold ${tone}`}>{value}</div>
    </div>
  );
}

function StrategyGoalCard({ goal }: { goal: StrategyGoalStatus }) {
  const title = labelStrategy(goal.strategy).toUpperCase();
  return (
    <section className="border border-poly-border bg-poly-black p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-mono text-[10px] uppercase tracking-[0.25em] text-poly-dim">{title}</div>
          <div className="mt-1 font-mono text-xs text-poly-dim">score {goal.score}</div>
        </div>
        <span className={`px-3 py-1 font-mono text-xs font-bold ${goal.go ? "bg-poly-green text-poly-black" : "bg-poly-red text-white"}`}>
          {goal.go ? "GO" : "NO-GO"}
        </span>
      </div>
      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        {goal.checks.map((check) => (
          <div key={`${goal.strategy}-${check.label}`} className="border border-poly-border px-3 py-2">
            <div className="font-mono text-[8px] uppercase text-poly-dim">{check.label}</div>
            <div className="mt-1 flex items-center justify-between gap-2">
              <span className="font-mono text-sm text-poly-text">{check.value}</span>
              <span className={`font-mono text-[10px] font-bold ${check.ok ? "text-poly-green" : "text-poly-red"}`}>
                {check.ok ? "OK" : "FAIL"}
              </span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function LogPanel({ title, items }: { title: string; items: Array<{ time: string; text: string }> }) {
  return (
    <section className="border border-poly-border bg-poly-black p-4">
      <div className="flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.25em] text-poly-dim">
        <span>{title}</span>
        <span className="text-poly-cyan">{items.length}</span>
      </div>
      <div className="mt-3 space-y-2">
        {items.length > 0 ? (
          items.map((item, index) => (
            <div key={`${title}-${index}-${item.time}`} className="border border-poly-border px-3 py-2">
              <div className="flex items-center justify-between gap-3 font-mono text-[9px] text-poly-dim">
                <span>{new Date(item.time).toLocaleTimeString()}</span>
                <span className="text-poly-cyan">LIVE</span>
              </div>
              <div className="mt-1 font-mono text-[11px] text-poly-text">{item.text}</div>
            </div>
          ))
        ) : (
          <div className="border border-poly-border px-3 py-4 text-center font-mono text-[10px] text-poly-dim">NO_LOGS</div>
        )}
      </div>
    </section>
  );
}
