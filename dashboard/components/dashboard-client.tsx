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
  strategy_id: string;
  strategy_version: string;
  regime: "trend" | "mean_revert" | "illiquid_choppy";
  model_probability: number;
  market_probability: number;
  edge: number;
  confidence: number;
  price: number;
  volume_24h: number;
  expected_slippage_bps?: number;
  expected_holding_minutes?: number;
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
  risk_fraction: number;
  take_profit_price?: number | null;
  stop_loss_price?: number | null;
  time_stop_minutes?: number | null;
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
  action?: "entry" | "scale_in" | "scale_out" | "close";
  position_key?: string;
  strategy_id?: string;
  regime?: string;
  direction: "YES" | "NO";
  size: number;
  price_limit: number;
  status: string;
  notional_usd: number;
  realized_pnl_usd?: number;
  exit_reason?: string;
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
  position_key?: string;
  market_question: string;
  asset_symbol?: string;
  crypto_tier?: "btc" | "major" | "small_cap";
  strategy_id?: string;
  regime?: string;
  direction: "YES" | "NO";
  size: number;
  average_price: number;
  current_price: number;
  current_value_usd: number;
  unrealized_pnl: number;
  cost_basis_usd?: number;
  take_profit_price?: number | null;
  stop_loss_price?: number | null;
  time_stop_minutes?: number | null;
  opened_at?: string;
  scaled_out_count?: number;
};

type PerformanceReport = {
  generated_at: string;
  window_hours: number;
  asset_filter: string;
  tier_filter: string;
  strategy_filter?: string;
  summary: {
    signals: number;
    decisions: number;
    orders: number;
    risk_events: number;
    approval_rate: number;
    execution_rate: number;
    positive_position_rate: number;
    win_rate?: number;
    avg_edge: number;
    avg_confidence: number;
    total_order_notional: number;
    avg_order_notional: number;
    daily_spend_usd?: number;
    realized_pnl_window?: number;
    sharpe_ratio?: number;
    max_drawdown?: number;
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
  strategy_breakdown?: Array<{ label: string; signals: number; orders: number; realized_pnl_usd: number }>;
  regime_breakdown?: Array<{ label: string; count: number }>;
  exit_reason_breakdown?: Array<{ label: string; count: number }>;
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
  mae_mfe?: {
    avg_mae: number;
    avg_mfe: number;
  };
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
const EquityChart = dynamic(() => import("./charts").then((m) => m.EquityChart), { ssr: false });
const PipelineChart = dynamic(() => import("./charts").then((m) => m.PipelineChart), { ssr: false });
const CostBarChart = dynamic(() => import("./charts").then((m) => m.CostBarChart), { ssr: false });
const RiskBreakdownChart = dynamic(() => import("./charts").then((m) => m.RiskBreakdownChart), { ssr: false });
const BreakdownMiniBar = dynamic(() => import("./charts").then((m) => m.BreakdownMiniBar), { ssr: false });

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
  const prefix = value >= 0 ? "+" : "";
  return prefix + asCurrency(value);
}

function labelStrategy(value?: string) {
  if (!value) return "—";
  return value.replaceAll("_", " ");
}

function labelRegime(value?: string) {
  if (!value) return "—";
  return value.replaceAll("_", " ");
}

function Icon({ name, className }: { name: string; className?: string }) {
  return <span className={`material-symbols-outlined ${className ?? ""}`}>{name}</span>;
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

        if (!active) return;

        const newLogs: LogEntry[] = [];
        signals.forEach(s => newLogs.push({
          id: `sig-${s.signal_id}`,
          time: new Date(s.created_at).toLocaleTimeString(),
          type: "signal",
          message: `NEW SIGNAL: ${s.asset_symbol} ${s.direction} | ${s.strategy_id}/${s.regime} | Edge: ${s.edge.toFixed(3)} | Model ${asPercent(s.model_probability)} vs Mkt ${asPercent(s.market_probability)}`,
          raw_time: s.created_at
        }));
        decisions.forEach(d => newLogs.push({
          id: `dec-${d.signal_id}-${d.created_at}`,
          time: new Date(d.created_at).toLocaleTimeString(),
          type: "decision",
          message: `DECISION: ${d.asset_symbol} ${d.approved ? "APPROVED" : "REJECTED"} | Kelly: ${d.kelly_size.toFixed(0)} | Risk: ${asPercent(d.risk_fraction ?? 0)} | ${d.notes}`,
          raw_time: d.created_at
        }));
        orders.forEach(o => newLogs.push({
          id: `ord-${o.order_id}`,
          time: new Date(o.created_at).toLocaleTimeString(),
          type: "order",
          message: `ORDER: ${o.asset_symbol} ${o.direction} ${o.action ?? "entry"} | ${o.size} shares @ ${o.price_limit.toFixed(3)} | Realized: ${asCurrencySigned(o.realized_pnl_usd ?? 0)} | ${o.status}`,
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

        setState({ statuses, costs, signals, decisions, orders, riskEvents, portfolio, positions, performance, logs: newLogs.slice(0, 100) });
        setError(null);
        setLastUpdated(new Date().toLocaleTimeString());
      } catch (loadError) {
        if (!active) return;
        setError(loadError instanceof Error ? loadError.message : "Unknown error");
      }
    }

    void load();
    const interval = window.setInterval(() => void load(), 5000);
    return () => { active = false; window.clearInterval(interval); };
  }, []);

  const perf = state.performance;
  const summary = perf?.summary;
  const runningAgents = Object.values(state.statuses).filter(s => s.running).length;
  const totalAgents = Object.keys(state.statuses).length;
  const balance = state.portfolio?.available_balance ?? 0;
  const pnl = state.portfolio?.total_pnl ?? 0;
  const equity = state.portfolio?.total_equity ?? 0;
  const drawdown = equity > 0 ? ((state.portfolio?.total_exposure ?? 0) / equity * 100) : 0;
  const sharpe = perf?.summary.sharpe_ratio ?? 0;
  const maxDrawdown = perf?.summary.max_drawdown ?? 0;

  return (
    <>
      <div className="crt-overlay" />

      {/* ── HEADER ── */}
      <header className="fixed top-0 left-0 right-0 z-50 flex items-center justify-between h-12 px-6 bg-poly-black border-b border-poly-border font-mono text-xs uppercase tracking-widest">
        <div className="flex items-center gap-6">
          <span className="text-lg font-black text-poly-cyan drop-glow-cyan">POLYTERM_v1.04</span>
          <div className="flex items-center gap-1 text-poly-dim">
            <span className={`w-1.5 h-1.5 ${runningAgents > 0 ? "bg-poly-green animate-pulse-dot" : "bg-poly-red"} rounded-full`} />
            <span className={runningAgents > 0 ? "text-poly-green" : "text-poly-red"}>
              {runningAgents > 0 ? "ONLINE" : "DEGRADED"}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-5">
          <div className="hidden md:flex items-center gap-4 text-poly-dim text-[10px]">
            <span>AGENTS: <span className="text-poly-cyan">{runningAgents}/{totalAgents}</span></span>
            <span className="opacity-30">|</span>
            <span>SIGNALS: <span className="text-poly-green">{summary?.signals ?? 0}</span></span>
            <span className="opacity-30">|</span>
            <span>LLM: <span className="text-poly-amber">{asCurrency(summary?.llm_cost_usd ?? 0)}</span></span>
          </div>
          <div className="text-poly-dim border border-poly-border px-2 py-0.5 bg-poly-surface-dim/50 text-[10px]">
            {error ? <span className="text-poly-red">API_ERROR</span> : <span>UPD_{lastUpdated}</span>}
          </div>
          <div className="text-poly-green font-bold glow-green text-sm">{asCurrency(balance)}</div>
        </div>
      </header>

      {/* ── MAIN CANVAS (full width, no sidebar) ── */}
      <main className="pt-12 w-full h-screen px-3 py-3 grid grid-cols-12 auto-rows-min gap-3 custom-scrollbar overflow-y-auto pb-10">

        {/* ══════ ROW 1: Hero Balance + KPI Strip ══════ */}
        <section className="col-span-5 border border-poly-border bg-poly-black p-5 flex flex-col justify-between relative overflow-hidden min-h-[180px]">
          <div className="absolute top-0 right-0 w-48 h-full opacity-10 pointer-events-none">
            <svg className="w-full h-full" preserveAspectRatio="none" viewBox="0 0 200 100">
              <path d="M0,80 L20,75 L40,85 L60,40 L80,55 L100,20 L120,45 L140,10 L160,30 L180,5 L200,15" fill="none" stroke="#00FF41" strokeWidth="2" />
            </svg>
          </div>
          <div>
            <span className="font-mono text-[9px] text-poly-dim tracking-widest uppercase mb-1 block">Available_Liquidity</span>
            <h1 className="text-5xl font-bold font-mono text-poly-green glow-green tracking-tighter">{asCurrency(balance)}</h1>
          </div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 mt-4 border-t border-poly-border pt-3">
            <Kpi label="Net_Profit" value={asCurrencySigned(pnl)} color={pnl >= 0 ? "text-poly-green" : "text-poly-red"} />
            <Kpi label="Win_Rate" value={summary ? asPercent(summary.positive_position_rate) : "0.0%"} color="text-poly-green" />
            <Kpi label="Realized" value={asCurrencySigned(state.portfolio?.realized_pnl ?? 0)} color={(state.portfolio?.realized_pnl ?? 0) >= 0 ? "text-poly-green" : "text-poly-red"} />
            <Kpi label="Unrealized" value={asCurrencySigned(state.portfolio?.unrealized_pnl ?? 0)} color={(state.portfolio?.unrealized_pnl ?? 0) >= 0 ? "text-poly-cyan" : "text-poly-red"} />
          </div>
        </section>

        {/* KPI metrics grid */}
        <section className="col-span-7 grid grid-cols-4 gap-3">
          <KpiCard label="Total_Equity" value={asCurrency(equity)} icon="account_balance" color="text-poly-cyan" />
          <KpiCard label="Exposure" value={`${drawdown.toFixed(1)}%`} icon="shield" color="text-poly-amber" />
          <KpiCard label="Open_Positions" value={`${state.portfolio?.open_positions ?? 0}`} icon="swap_vert" color="text-poly-green" />
          <KpiCard label="Risk_Events" value={`${summary?.risk_events ?? state.riskEvents.length}`} icon="warning" color="text-poly-red" />
          <KpiCard label="Approval_Rate" value={summary ? asPercent(summary.approval_rate) : "—"} icon="check_circle" color="text-poly-green" />
          <KpiCard label="Exec_Rate" value={summary ? asPercent(summary.execution_rate) : "—"} icon="bolt" color="text-poly-cyan" />
          <KpiCard label="Avg_Edge" value={summary ? summary.avg_edge.toFixed(3) : "—"} icon="trending_up" color="text-poly-green" />
          <KpiCard label="Avg_Confidence" value={summary ? asPercent(summary.avg_confidence) : "—"} icon="psychology" color="text-poly-amber" />
          <KpiCard label="Sharpe" value={sharpe.toFixed(2)} icon="query_stats" color="text-poly-cyan" />
          <KpiCard label="Max_Drawdown" value={asPercent(maxDrawdown)} icon="monitoring" color="text-poly-red" />
          <KpiCard label="Win_Rate" value={summary?.win_rate != null ? asPercent(summary.win_rate) : "—"} icon="workspace_premium" color="text-poly-green" />
          <KpiCard label="Spend_24h" value={asCurrency(summary?.daily_spend_usd ?? 0)} icon="payments" color="text-poly-amber" />
        </section>

        {/* ══════ ROW 2: Equity Curve + Signal Feed ══════ */}
        <section className="col-span-8 border border-poly-border bg-poly-black relative min-h-[220px] flex flex-col">
          <div className="absolute top-3 left-3 font-mono text-[10px] text-poly-dim uppercase z-10">Equity_Performance_Matrix</div>
          <div className="flex-1 pt-7 px-2 pb-2">
            <EquityChart equity={perf?.time_series.equity ?? []} />
          </div>
          <div className="absolute inset-0 bg-gradient-to-t from-poly-green/5 to-transparent pointer-events-none" />
        </section>

        <section className="col-span-4 border border-poly-border bg-poly-black flex flex-col overflow-hidden min-h-[220px]">
          <div className="p-2.5 border-b border-poly-border font-mono text-[10px] text-poly-dim uppercase flex justify-between">
            <span>Signal_Decision_Feed</span>
            <span className="text-poly-cyan animate-pulse-dot">LIVE</span>
          </div>
          <div className="flex-1 font-mono text-[10px] overflow-y-auto custom-scrollbar">
            <div className="space-y-0.5 p-2">
                {state.signals.slice(0, 5).map(s => (
                  <div key={s.signal_id} className="flex justify-between text-poly-cyan/80 bg-poly-cyan/5 px-1">
                    <span className="truncate max-w-[68%]">{s.asset_symbol} {s.direction} [{labelRegime(s.regime)}]</span>
                    <span>E:{s.edge.toFixed(3)}</span>
                  </div>
                ))}
              {state.signals.length === 0 && <div className="text-poly-dim text-center py-2">NO_SIGNALS</div>}
            </div>
            <div className="py-1 px-3 border-y border-poly-border bg-poly-surface-container/40 text-center text-[10px] text-poly-muted font-bold">
              APPROVAL: {summary ? asPercent(summary.approval_rate) : "—"}
            </div>
            <div className="space-y-0.5 p-2">
              {state.decisions.slice(0, 5).map(d => (
                <div key={`${d.signal_id}-${d.created_at}`} className={`flex justify-between px-1 ${d.approved ? "text-poly-green/80 bg-poly-green/5" : "text-poly-red/80 bg-poly-red/5"}`}>
                  <span className="truncate max-w-[60%]">{d.asset_symbol} {d.approved ? "OK" : "REJ"}</span>
                  <span>K:{d.kelly_size.toFixed(0)}</span>
                </div>
              ))}
              {state.decisions.length === 0 && <div className="text-poly-dim text-center py-2">NO_DECISIONS</div>}
            </div>
          </div>
          <div className="p-2 border-t border-poly-border mt-auto">
            <div className="font-mono text-[8px] text-poly-dim uppercase mb-0.5">Risk_Alerts</div>
            {state.riskEvents.slice(0, 2).map((ev, i) => (
              <div key={i} className="font-mono text-[8px] text-poly-red/70 truncate">[{ev.agent}] {ev.reason}</div>
            ))}
            {state.riskEvents.length === 0 && <div className="font-mono text-[8px] text-poly-dim">CLEAR</div>}
          </div>
        </section>

        {/* ══════ ROW 3: Pipeline + LLM Cost + Risk Breakdown ══════ */}
        <section className="col-span-4 border border-poly-border bg-poly-black flex flex-col min-h-[200px]">
          <PanelHead title="Pipeline_Activity_24h" badge={<Legend items={[{c:"#00ff41",l:"SIG"},{c:"#00f3ff",l:"DEC"},{c:"#fbbf24",l:"ORD"},{c:"#ff3131",l:"RSK"}]} />} />
          <div className="flex-1 p-1">
            <PipelineChart data={perf?.time_series.pipeline ?? []} />
          </div>
        </section>

        <section className="col-span-4 border border-poly-border bg-poly-black flex flex-col min-h-[200px]">
          <PanelHead title="LLM_Cost_By_Agent" badge={<span className="text-poly-amber">{asCurrency(summary?.llm_cost_usd ?? 0)}</span>} />
          <div className="flex-1 p-1">
            <CostBarChart data={perf?.cost_by_agent ?? state.costs} />
          </div>
        </section>

        <section className="col-span-4 border border-poly-border bg-poly-black flex flex-col min-h-[200px]">
          <PanelHead title="Risk_Breakdown" badge={<span className="text-poly-red">{summary?.risk_events ?? 0} events</span>} />
          <div className="flex-1 p-1">
            <RiskBreakdownChart data={perf?.risk_breakdown ?? []} />
          </div>
        </section>

        {/* ══════ ROW 4: Orders Table + Agents + Top Markets ══════ */}
        <section className="col-span-6 border border-poly-border bg-poly-black flex flex-col max-h-[280px]">
          <PanelHead title="Recent_Executions" badge={<span>{summary?.orders ?? state.orders.length} total</span>} />
          <div className="flex-1 overflow-y-auto custom-scrollbar">
            <table className="w-full font-mono text-[10px] text-left border-collapse">
              <thead className="bg-poly-surface-container/50 text-poly-dim uppercase sticky top-0">
                <tr>
                  <th className="p-1.5 font-normal">TIME</th>
                  <th className="p-1.5 font-normal">ASSET</th>
                  <th className="p-1.5 font-normal">ACT</th>
                  <th className="p-1.5 font-normal">DIR</th>
                  <th className="p-1.5 font-normal text-right">SIZE</th>
                  <th className="p-1.5 font-normal text-right">PRICE</th>
                  <th className="p-1.5 font-normal text-right">RPNL</th>
                  <th className="p-1.5 font-normal text-center">STS</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-poly-border-dim">
                {state.orders.slice(0, 8).map(o => (
                  <tr key={o.order_id} className="hover:bg-poly-surface-container/40">
                    <td className="p-1.5 text-poly-dim">{new Date(o.created_at).toLocaleTimeString()}</td>
                    <td className="p-1.5 font-bold text-poly-cyan">{o.asset_symbol}</td>
                    <td className="p-1.5 text-poly-dim uppercase">{o.action ?? "entry"}</td>
                    <td className={`p-1.5 ${o.direction === "YES" ? "text-poly-green" : "text-poly-red"}`}>{o.direction}</td>
                    <td className="p-1.5 text-right">{o.size}</td>
                    <td className="p-1.5 text-right">{o.price_limit.toFixed(3)}</td>
                    <td className={`p-1.5 text-right ${(o.realized_pnl_usd ?? 0) >= 0 ? "text-poly-green" : "text-poly-red"}`}>
                      {asCurrencySigned(o.realized_pnl_usd ?? 0)}
                    </td>
                    <td className="p-1.5 text-center">
                      <span className={`px-1 text-[8px] font-bold ${o.status === "filled" || o.status === "simulated" ? "bg-poly-green text-poly-black" : o.status === "failed" ? "bg-poly-red text-white" : "bg-poly-surface-bright text-poly-muted"}`}>{o.status.toUpperCase()}</span>
                    </td>
                  </tr>
                ))}
                {state.orders.length === 0 && <tr><td colSpan={8} className="p-3 text-center text-poly-dim">AWAITING...</td></tr>}
              </tbody>
            </table>
          </div>
        </section>

        <section className="col-span-2 border border-poly-border bg-poly-surface-dim/20 flex flex-col max-h-[280px]">
          <PanelHead title="Agent_Status" badge={<span className="text-poly-green">{runningAgents}/{totalAgents}</span>} />
          <div className="flex-1 p-2 font-mono text-[10px] overflow-y-auto custom-scrollbar">
            {Object.entries(state.statuses).map(([name, status]) => (
              <div key={name} className="flex justify-between border-b border-poly-border-dim py-1.5">
                <span className="flex items-center gap-1.5">
                  <span className={`w-1.5 h-1.5 rounded-full ${status.running ? "bg-poly-green animate-pulse" : "bg-poly-red"}`} />
                  <span className="text-poly-muted truncate max-w-[80px]">{name}</span>
                </span>
                <span className={status.running ? "text-poly-green" : "text-poly-dim"}>{status.running ? "ON" : "OFF"}</span>
              </div>
            ))}
            {totalAgents === 0 && <div className="text-poly-dim text-center py-3">NO_AGENTS</div>}
          </div>
        </section>

        <section className="col-span-4 border border-poly-border bg-poly-black flex flex-col max-h-[280px]">
          <PanelHead title="Top_Markets" badge={<span>{perf?.top_markets?.length ?? 0} tracked</span>} />
          <div className="flex-1 overflow-y-auto custom-scrollbar">
            <table className="w-full font-mono text-[9px] text-left border-collapse">
              <thead className="bg-poly-surface-container/50 text-poly-dim uppercase sticky top-0">
                <tr>
                  <th className="p-1.5 font-normal">ASSET</th>
                  <th className="p-1.5 font-normal text-right">SIGS</th>
                  <th className="p-1.5 font-normal text-right">ORDS</th>
                  <th className="p-1.5 font-normal text-right">EDGE</th>
                  <th className="p-1.5 font-normal text-right">CONF</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-poly-border-dim">
                {(perf?.top_markets ?? []).slice(0, 8).map(m => (
                  <tr key={m.market_id} className="hover:bg-poly-surface-container/40">
                    <td className="p-1.5 text-poly-cyan truncate max-w-[120px]" title={m.market_question}>{m.asset_symbol}</td>
                    <td className="p-1.5 text-right text-poly-green">{m.signal_count}</td>
                    <td className="p-1.5 text-right text-poly-amber">{m.order_count}</td>
                    <td className="p-1.5 text-right">{m.avg_edge.toFixed(3)}</td>
                    <td className="p-1.5 text-right">{asPercent(m.avg_confidence)}</td>
                  </tr>
                ))}
                {(perf?.top_markets ?? []).length === 0 && <tr><td colSpan={5} className="p-3 text-center text-poly-dim">NO_DATA</td></tr>}
              </tbody>
            </table>
          </div>
        </section>

        {/* ══════ ROW 5: Asset / Strategy / Regime Breakdowns ══════ */}
        <section className="col-span-4 border border-poly-border bg-poly-black flex flex-col">
          <PanelHead title="Asset_Distribution" badge={<span>{perf?.asset_breakdown?.length ?? 0} assets</span>} />
          <div className="flex-1 p-3">
            <BreakdownMiniBar data={perf?.asset_breakdown ?? []} color="#00f3ff" />
          </div>
        </section>

        <section className="col-span-4 border border-poly-border bg-poly-black flex flex-col">
          <PanelHead title="Strategy_Distribution" badge={<span>{perf?.strategy_breakdown?.length ?? 0} models</span>} />
          <div className="flex-1 p-3">
            <BreakdownMiniBar
              data={(perf?.strategy_breakdown ?? []).map((item) => ({ label: labelStrategy(item.label), count: item.orders }))}
              color="#00ff41"
            />
          </div>
        </section>

        <section className="col-span-4 border border-poly-border bg-poly-black flex flex-col">
          <PanelHead title="Regime_Distribution" badge={<span className="text-poly-amber">{perf?.regime_breakdown?.length ?? 0} regimes</span>} />
          <div className="flex-1 p-3">
            <BreakdownMiniBar
              data={(perf?.regime_breakdown ?? []).map((item) => ({ label: labelRegime(item.label), count: item.count }))}
              color="#fbbf24"
            />
          </div>
        </section>

        {/* ══════ ROW 6: Tier / News / Exits ══════ */}
        <section className="col-span-4 border border-poly-border bg-poly-black flex flex-col">
          <PanelHead title="Tier_Distribution" badge={<span>{perf?.tier_breakdown?.length ?? 0} tiers</span>} />
          <div className="flex-1 p-3">
            <BreakdownMiniBar data={perf?.tier_breakdown ?? []} color="#00ff41" />
          </div>
        </section>

        <section className="col-span-4 border border-poly-border bg-poly-black flex flex-col">
          <PanelHead title="News_Validation" badge={<span className="text-poly-amber">{perf?.last_news_provider?.provider_used ?? "—"}</span>} />
          <div className="flex-1 p-3 space-y-3">
            <div>
              <div className="font-mono text-[8px] text-poly-dim uppercase mb-1">Validation_Results</div>
              <BreakdownMiniBar data={perf?.news_breakdown ?? []} color="#fbbf24" />
            </div>
            <div>
              <div className="font-mono text-[8px] text-poly-dim uppercase mb-1">Provider_Usage</div>
              <BreakdownMiniBar data={perf?.news_provider_breakdown ?? []} color="#00f3ff" />
            </div>
            {perf?.last_news_provider && (
              <div className="font-mono text-[8px] text-poly-dim border-t border-poly-border pt-2">
                LAST: {perf.last_news_provider.asset_symbol} via {perf.last_news_provider.provider_used}
                {perf.last_news_provider.fallback_used && <span className="text-poly-amber ml-1">[FALLBACK]</span>}
              </div>
            )}
          </div>
        </section>

        <section className="col-span-4 border border-poly-border bg-poly-black flex flex-col">
          <PanelHead title="Exit_Reasons" badge={<span className="text-poly-cyan">{asCurrencySigned(summary?.realized_pnl_window ?? 0)}</span>} />
          <div className="flex-1 p-3 space-y-3">
            <div>
              <div className="font-mono text-[8px] text-poly-dim uppercase mb-1">Exit_Breakdown</div>
              <BreakdownMiniBar data={perf?.exit_reason_breakdown ?? []} color="#ff3131" />
            </div>
            <div className="grid grid-cols-2 gap-3 pt-2 border-t border-poly-border">
              <Kpi label="Avg_MAE" value={(perf?.mae_mfe?.avg_mae ?? 0).toFixed(4)} color="text-poly-red" />
              <Kpi label="Avg_MFE" value={(perf?.mae_mfe?.avg_mfe ?? 0).toFixed(4)} color="text-poly-green" />
            </div>
          </div>
        </section>

        {/* ══════ ROW 7: Positions ══════ */}
        <section className="col-span-12 border border-poly-border bg-poly-black flex flex-col">
          <PanelHead title="Open_Positions" badge={<span>{state.positions.length} ACTIVE</span>} />
          <div className="flex-1 overflow-y-auto custom-scrollbar">
            <table className="w-full font-mono text-[10px] text-left border-collapse">
              <thead className="bg-poly-surface-container/50 text-poly-dim uppercase sticky top-0">
                <tr>
                  <th className="p-2 font-normal">MARKET</th>
                  <th className="p-2 font-normal">STRAT</th>
                  <th className="p-2 font-normal">REGIME</th>
                  <th className="p-2 font-normal">DIR</th>
                  <th className="p-2 font-normal text-right">SIZE</th>
                  <th className="p-2 font-normal text-right">ENTRY</th>
                  <th className="p-2 font-normal text-right">MARK</th>
                  <th className="p-2 font-normal text-right">TP/SL</th>
                  <th className="p-2 font-normal text-right">VALUE</th>
                  <th className="p-2 font-normal text-right">P&L</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-poly-border-dim">
                {state.positions.map(pos => (
                  <tr key={`${pos.market_id}-${pos.direction}`} className="hover:bg-poly-surface-container/40">
                    <td className="p-2 text-poly-cyan truncate max-w-[300px]" title={pos.market_question}>{pos.asset_symbol ?? "?"} <span className="text-poly-dim">{pos.market_question}</span></td>
                    <td className="p-2 text-poly-muted">{labelStrategy(pos.strategy_id)}</td>
                    <td className="p-2 text-poly-amber">{labelRegime(pos.regime)}</td>
                    <td className={`p-2 ${pos.direction === "YES" ? "text-poly-green" : "text-poly-red"}`}>{pos.direction}</td>
                    <td className="p-2 text-right">{pos.size}</td>
                    <td className="p-2 text-right">{pos.average_price.toFixed(3)}</td>
                    <td className="p-2 text-right">{pos.current_price.toFixed(3)}</td>
                    <td className="p-2 text-right text-poly-dim">
                      {(pos.take_profit_price ?? 0).toFixed(3)}/{(pos.stop_loss_price ?? 0).toFixed(3)}
                    </td>
                    <td className="p-2 text-right text-poly-muted">{asCurrency(pos.current_value_usd)}</td>
                    <td className={`p-2 text-right font-bold ${pos.unrealized_pnl >= 0 ? "text-poly-green" : "text-poly-red"}`}>{asCurrencySigned(pos.unrealized_pnl)}</td>
                  </tr>
                ))}
                {state.positions.length === 0 && <tr><td colSpan={10} className="p-4 text-center text-poly-dim">SCANNING_FOR_ENTRIES...</td></tr>}
              </tbody>
            </table>
          </div>
        </section>

        {/* ══════ ROW 8: Log Terminal ══════ */}
        <section className="col-span-12 border border-poly-border bg-poly-black flex flex-col max-h-[300px]">
          <PanelHead title="System_Log_Terminal" badge={<span className="text-poly-cyan animate-pulse-dot">LIVE</span>} />
          <LogTerminal logs={state.logs} />
        </section>
      </main>

      {/* ── FOOTER ── */}
      <footer className="fixed bottom-0 left-0 w-full bg-poly-surface-dim border-t border-poly-border h-6 flex items-center px-4 z-50 overflow-hidden">
        <div className="flex-1 flex gap-6 font-mono text-[9px] items-center">
          <div className="flex items-center gap-1">
            <span className={`w-1.5 h-1.5 ${runningAgents > 0 ? "bg-poly-green animate-pulse-dot" : "bg-poly-red"} rounded-full`} />
            <span className={runningAgents > 0 ? "text-poly-green" : "text-poly-red"}>
              {runningAgents > 0 ? "SYSTEM_ONLINE" : "DEGRADED"}
            </span>
          </div>
          <span className="text-poly-dim opacity-30">|</span>
          <span className="text-poly-cyan">AGENTS:{runningAgents}/{totalAgents}</span>
          <span className="text-poly-dim opacity-30">|</span>
          <span className="text-poly-dim">NOTIONAL:{asCurrency(summary?.total_order_notional ?? 0)}</span>
          <span className="text-poly-dim opacity-30">|</span>
          <span className="text-poly-dim">LOGS:{state.logs.length}</span>
        </div>
        <div className="flex items-center gap-4 font-mono text-[9px]">
          {state.riskEvents.length > 0 && (
            <div className="bg-poly-red text-white px-2 animate-pulse font-bold text-[8px]">RISK:{state.riskEvents.length}</div>
          )}
          <span className="text-poly-muted">{new Date().toLocaleTimeString()}</span>
        </div>
      </footer>
    </>
  );
}

function Kpi({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div>
      <span className="font-mono text-[8px] text-poly-dim uppercase block">{label}</span>
      <span className={`text-base font-bold font-mono ${color}`}>{value}</span>
    </div>
  );
}

function KpiCard({ label, value, icon, color }: { label: string; value: string; icon: string; color: string }) {
  return (
    <div className="border border-poly-border bg-poly-black p-3 flex flex-col justify-between min-h-[80px]">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[8px] text-poly-dim uppercase">{label}</span>
        <Icon name={icon} className={`text-sm ${color} opacity-40`} />
      </div>
      <span className={`text-lg font-bold font-mono ${color}`}>{value}</span>
    </div>
  );
}

function PanelHead({ title, badge }: { title: string; badge?: React.ReactNode }) {
  return (
    <div className="p-2.5 border-b border-poly-border font-mono text-[10px] text-poly-dim uppercase flex justify-between items-center">
      <span>{title}</span>
      {badge && <span className="text-[9px]">{badge}</span>}
    </div>
  );
}

function Legend({ items }: { items: Array<{ c: string; l: string }> }) {
  return (
    <div className="flex gap-2">
      {items.map(i => (
        <span key={i.l} className="flex items-center gap-0.5 text-[8px]">
          <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ backgroundColor: i.c }} />
          {i.l}
        </span>
      ))}
    </div>
  );
}

function LogTerminal({ logs }: { logs: LogEntry[] }) {
  const terminalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = 0;
    }
  }, [logs]);

  const tagColor: Record<string, string> = {
    signal: "text-poly-cyan border-poly-cyan",
    decision: "text-poly-amber border-poly-amber",
    order: "text-poly-green border-poly-green",
    risk: "text-poly-red border-poly-red",
  };

  return (
    <div className="flex-1 overflow-y-auto custom-scrollbar p-2 bg-poly-black" ref={terminalRef}>
      {logs.map((log) => (
        <div key={log.id} className="font-mono text-[10px] leading-5 whitespace-pre-wrap break-all">
          <span className="text-poly-dim">[{log.time}]</span>{" "}
          <span className={`px-1 border font-bold text-[8px] uppercase ${tagColor[log.type] ?? "text-poly-dim border-poly-dim"}`}>{log.type}</span>{" "}
          <span className="text-poly-text/80">{log.message}</span>
        </div>
      ))}
      {logs.length === 0 && <div className="font-mono text-[10px] text-poly-dim text-center py-8">Waiting_for_system_logs...</div>}
    </div>
  );
}

