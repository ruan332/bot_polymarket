"use client";

import { useEffect, useRef, useState } from "react";
import { FlowAnalysisChart } from "./charts";

type AgentStatus = {
  running: boolean;
};

type Signal = {
  signal_id: string;
  asset_symbol: string;
  strategy_id: string;
  market_probability?: number;
  model_probability?: number;
  expected_slippage_bps?: number;
  expected_holding_minutes?: number;
  volume_24h?: number;
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
  market_id?: string;
  trade_group_id?: string;
  leg_role?: string;
  direction: "YES" | "NO";
  action?: "entry" | "scale_in" | "scale_out" | "close";
  notional_usd?: number;
  realized_pnl_usd?: number;
  status: string;
  exchange_order_id?: string;
  reason?: string;
  created_at: string;
};

type OpenPosition = {
  market_id: string;
  position_key: string;
  token_id: string;
  market_question: string;
  asset_symbol: string;
  crypto_tier: string;
  strategy_id: string;
  regime?: string;
  trade_group_id?: string;
  cycle_slug?: string;
  leg_role?: string;
  direction: "YES" | "NO";
  size: number;
  average_price: number;
  current_price: number;
  cost_basis_usd: number;
  current_value_usd: number;
  unrealized_pnl: number;
  take_profit_price?: number | null;
  stop_loss_price?: number | null;
  time_stop_minutes?: number | null;
  opened_at: string;
  scaled_out_count: number;
  latest_spread_bps?: number;
  updated_at: string;
};

type RiskEvent = {
  reason: string;
  agent: string;
  created_at: string;
};

type FlowAnalysis = {
  flow_id: string;
  signal_id?: string | null;
  trade_group_id?: string | null;
  market_id: string;
  cycle_slug?: string;
  market_question?: string;
  asset_symbol: string;
  asset_name?: string;
  crypto_tier?: string;
  window_minutes?: number;
  dominant_direction: "up" | "down" | "neutral";
  dominance_score: number;
  confidence: number;
  up_trade_count: number;
  down_trade_count: number;
  up_notional: number;
  down_notional: number;
  total_trades: number;
  total_notional: number;
  freshness_seconds: number;
  source_used?: "ws" | "data_api" | "mixed";
  sample_count?: number;
  last_trade_at?: string | null;
  updated_at?: string | null;
  created_at?: string;
  metadata?: Record<string, unknown>;
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
  paper_orders?: number;
  live_orders?: number;
  live_submitted_orders?: number;
  live_filled_orders?: number;
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
  order_lifecycle_summary?: {
    tracked_orders: number;
    live_submitted_orders: number;
    live_filled_orders: number;
    blocked_orders: number;
    cancelled_orders: number;
    pending_cancelled_orders: number;
    fill_rate: number;
    cancel_rate: number;
    avg_fill_latency_seconds: number;
    avg_open_duration_seconds: number;
    cancel_reason_breakdown: Array<{ label: string; count: number }>;
  };
};

type WeatherCopytradeReport = {
  summary: string;
  why: string;
  risks: string[];
  selection_reason: string;
  selected_proxy_wallet: string;
  selected_user_name: string;
  model: string;
  provider: string;
  fallback_used: boolean;
};

type WeatherCopytradeCandidate = {
  run_id?: string;
  rank: number;
  proxy_wallet: string;
  user_name: string;
  verified_badge?: boolean;
  profile: Record<string, unknown>;
  metrics: Record<string, unknown>;
  score: number;
  rationale: string;
  selected?: boolean;
  created_at?: string;
  passed?: boolean;
  reject_reason?: string;
};

type WeatherCopytradeRun = {
  run_id: string;
  category: string;
  leaderboard_limit: number;
  universe_count: number;
  shortlisted_count: number;
  selected_count: number;
  selected_proxy_wallet: string;
  selected_user_name: string;
  candidate_count: number;
  stage_counts: Array<{ label: string; count: number }>;
  rejected_breakdown: Record<string, number>;
  model_summary: WeatherCopytradeReport | null;
  selection_summary: Record<string, unknown>;
  scan_stats: Record<string, unknown>;
  metadata: Record<string, unknown>;
  created_at: string;
};

type WeatherCopytradeState = {
  category: string;
  run_id?: string;
  selected_proxy_wallet: string;
  selected_user_name: string;
  selected_profile: Record<string, unknown>;
  selection: Record<string, unknown>;
  report: WeatherCopytradeReport | null;
  approved: boolean;
  active: boolean;
  paused: boolean;
  approved_at?: string | null;
  activated_at?: string | null;
  last_trade_seen_at?: string | null;
  last_trade_seen_hash?: string;
  processed_trade_hashes?: string[];
  metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
};

type WeatherCopytradeSummary = {
  run: WeatherCopytradeRun | null;
  candidates: WeatherCopytradeCandidate[];
  state: WeatherCopytradeState | null;
  report?: WeatherCopytradeReport | null;
  selection_summary?: Record<string, unknown>;
  scan_stats?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
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

type LogTone = "positive" | "negative" | "neutral" | "warning";

type LogLine = {
  time: string;
  tone: LogTone;
  tag: string;
  title: string;
  detail?: string;
  meta?: string;
};

type StrategyGoalCheck = {
  label: string;
  value: string;
  target: string;
  ok: boolean;
};

type StrategyGoalStatus = {
  strategy: "pair_15m" | "momentum_15m";
  subtitle: string;
  go: boolean;
  score: string;
  checks: StrategyGoalCheck[];
};

type DashboardState = {
  statuses: Record<string, AgentStatus>;
  portfolio: PortfolioSummary | null;
  liveBootstrap: {
    mode?: string;
    ready?: boolean;
    reason?: string;
    fail_open?: boolean;
  } | null;
  overview: MetricsOverview | null;
  performance: PerformanceReport | null;
  pairPerformance: PerformanceReport | null;
  momentumPerformance: PerformanceReport | null;
  weatherCopytradeSummary: WeatherCopytradeSummary | null;
  weatherCopytradeMetrics: PerformanceReport | null;
  signals: Signal[];
  decisions: Decision[];
  orders: Order[];
  positions: OpenPosition[];
  riskEvents: RiskEvent[];
  flowAnalyses: FlowAnalysis[];
};

type SignalMetricsFilter = "all" | "pair_15m" | "momentum_15m";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}`);
  }
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, body: unknown = {}): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body ?? {}),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail ? `Failed to POST ${path}: ${detail}` : `Failed to POST ${path}`);
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
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed) {
      const parsed = Number(trimmed);
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return fallback;
}

function optionalNumber(value: unknown): number | null {
  const parsed = numberOr(value, Number.NaN);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeRecord(value: unknown): Record<string, unknown> | null {
  if (!value) return null;
  if (typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return null;
    try {
      const parsed = JSON.parse(trimmed);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      return null;
    }
  }
  return null;
}

function normalizeObjectRecord(value: unknown): Record<string, unknown> {
  return normalizeRecord(value) ?? {};
}

function normalizeNumberRecord(value: unknown): Record<string, number> {
  const raw = normalizeRecord(value);
  if (!raw) return {};
  const normalized: Record<string, number> = {};
  Object.entries(raw).forEach(([key, item]) => {
    const parsed = optionalNumber(item);
    if (parsed != null) {
      normalized[key] = parsed;
    }
  });
  return normalized;
}

function normalizeWeatherCopytradeReport(value: unknown): WeatherCopytradeReport | null {
  const raw = normalizeRecord(value);
  if (!raw) return null;
  return {
    summary: String(raw.summary ?? ""),
    why: String(raw.why ?? ""),
    risks: Array.isArray(raw.risks) ? raw.risks.map((item) => String(item)).filter(Boolean) : [],
    selection_reason: String(raw.selection_reason ?? ""),
    selected_proxy_wallet: String(raw.selected_proxy_wallet ?? ""),
    selected_user_name: String(raw.selected_user_name ?? ""),
    model: String(raw.model ?? "deterministic"),
    provider: String(raw.provider ?? "deterministic"),
    fallback_used: Boolean(raw.fallback_used),
  };
}

function normalizeWeatherCopytradeCandidate(value: unknown): WeatherCopytradeCandidate | null {
  const raw = normalizeRecord(value);
  if (!raw) return null;
  return {
    run_id: typeof raw.run_id === "string" ? raw.run_id : undefined,
    rank: Math.trunc(optionalNumber(raw.rank) ?? 0),
    proxy_wallet: String(raw.proxy_wallet ?? ""),
    user_name: String(raw.user_name ?? ""),
    verified_badge: Boolean(raw.verified_badge),
    profile: normalizeObjectRecord(raw.profile),
    metrics: normalizeObjectRecord(raw.metrics),
    score: optionalNumber(raw.score) ?? 0,
    rationale: String(raw.rationale ?? ""),
    selected: typeof raw.selected === "boolean" ? raw.selected : undefined,
    created_at: typeof raw.created_at === "string" ? raw.created_at : undefined,
    passed: typeof raw.passed === "boolean" ? raw.passed : undefined,
    reject_reason: typeof raw.reject_reason === "string" ? raw.reject_reason : undefined,
  };
}

function normalizeWeatherCopytradeRun(value: unknown): WeatherCopytradeRun | null {
  const raw = normalizeRecord(value);
  if (!raw) return null;
  return {
    run_id: String(raw.run_id ?? ""),
    category: String(raw.category ?? "WEATHER"),
    leaderboard_limit: Math.trunc(optionalNumber(raw.leaderboard_limit) ?? 0),
    universe_count: Math.trunc(optionalNumber(raw.universe_count) ?? 0),
    shortlisted_count: Math.trunc(optionalNumber(raw.shortlisted_count) ?? 0),
    selected_count: Math.trunc(optionalNumber(raw.selected_count) ?? 0),
    selected_proxy_wallet: String(raw.selected_proxy_wallet ?? ""),
    selected_user_name: String(raw.selected_user_name ?? ""),
    candidate_count: Math.trunc(optionalNumber(raw.candidate_count) ?? 0),
    stage_counts: Array.isArray(raw.stage_counts)
      ? raw.stage_counts.map((item) => ({
          label: String(normalizeRecord(item)?.label ?? ""),
          count: Math.trunc(optionalNumber(normalizeRecord(item)?.count) ?? 0),
        }))
      : [],
    rejected_breakdown: normalizeNumberRecord(raw.rejected_breakdown),
    model_summary: normalizeWeatherCopytradeReport(raw.model_summary) ?? null,
    selection_summary: normalizeObjectRecord(raw.selection_summary),
    scan_stats: normalizeObjectRecord(raw.scan_stats),
    metadata: normalizeObjectRecord(raw.metadata),
    created_at: String(raw.created_at ?? ""),
  };
}

function normalizeWeatherCopytradeState(value: unknown): WeatherCopytradeState | null {
  const raw = normalizeRecord(value);
  if (!raw) return null;
  return {
    category: String(raw.category ?? "WEATHER"),
    run_id: typeof raw.run_id === "string" ? raw.run_id : undefined,
    selected_proxy_wallet: String(raw.selected_proxy_wallet ?? ""),
    selected_user_name: String(raw.selected_user_name ?? ""),
    selected_profile: normalizeObjectRecord(raw.selected_profile),
    selection: normalizeObjectRecord(raw.selection),
    report: normalizeWeatherCopytradeReport(raw.report) ?? null,
    approved: Boolean(raw.approved),
    active: Boolean(raw.active),
    paused: Boolean(raw.paused),
    approved_at: typeof raw.approved_at === "string" ? raw.approved_at : null,
    activated_at: typeof raw.activated_at === "string" ? raw.activated_at : null,
    last_trade_seen_at: typeof raw.last_trade_seen_at === "string" ? raw.last_trade_seen_at : null,
    last_trade_seen_hash: typeof raw.last_trade_seen_hash === "string" ? raw.last_trade_seen_hash : undefined,
    processed_trade_hashes: Array.isArray(raw.processed_trade_hashes) ? raw.processed_trade_hashes.map((item) => String(item)) : [],
    metadata: normalizeObjectRecord(raw.metadata),
    created_at: typeof raw.created_at === "string" ? raw.created_at : undefined,
    updated_at: typeof raw.updated_at === "string" ? raw.updated_at : undefined,
  };
}

function normalizeWeatherCopytradeSummary(value: unknown): WeatherCopytradeSummary | null {
  const raw = normalizeRecord(value);
  if (!raw) return null;
  const stateRecord = normalizeRecord(raw.state);
  const runRecord = normalizeRecord(raw.run);
  const reportSource = raw.report ?? stateRecord?.report ?? runRecord?.model_summary;
  return {
    run: normalizeWeatherCopytradeRun(raw.run),
    candidates: Array.isArray(raw.candidates)
      ? raw.candidates.map((item) => normalizeWeatherCopytradeCandidate(item)).filter((item): item is WeatherCopytradeCandidate => item !== null)
      : [],
    state: normalizeWeatherCopytradeState(raw.state),
    report: normalizeWeatherCopytradeReport(reportSource) ?? null,
    selection_summary: normalizeObjectRecord(raw.selection_summary ?? runRecord?.selection_summary),
    scan_stats: normalizeObjectRecord(raw.scan_stats ?? runRecord?.scan_stats),
    metadata: normalizeObjectRecord(raw.metadata ?? runRecord?.metadata),
  };
}

function labelStrategy(value?: string) {
  if (!value) return "unknown";
  return value.replaceAll("_", " ");
}

function signalDirection(signal: Signal) {
  return signal.direction ?? "-";
}

function flowDirectionLabel(flow: FlowAnalysis) {
  return flow.dominant_direction.toUpperCase();
}

function toneForSignal(signal: Signal): LogTone {
  const edge = numberOr(signal.edge);
  const confidence = numberOr(signal.confidence);
  if (edge >= 0.12 && confidence >= 0.7) return "positive";
  if (edge <= 0.08 || confidence < 0.6) return "negative";
  return "neutral";
}

function toneForDecision(decision: Decision): LogTone {
  return decision.approved ? "positive" : "negative";
}

function toneForOrder(order: Order): LogTone {
  if (order.status === "live_submitted") return "warning";
  if (order.status === "cancelled" || order.status === "expired" || order.status === "rejected") return "negative";
  const pnl = numberOr(order.realized_pnl_usd);
  if (pnl > 0) return "positive";
  if (pnl < 0) return "negative";
  return order.action === "close" ? "warning" : "neutral";
}

function toneForRiskEvent(event: RiskEvent): LogTone {
  const reason = event.reason.toLowerCase();
  if (reason.includes("approved") || reason.includes("passed") || reason.includes("ok")) return "positive";
  if (reason.includes("blocked") || reason.includes("exceeds") || reason.includes("fail") || reason.includes("rejected")) {
    return "negative";
  }
  return "warning";
}

function toneForFlow(flow: FlowAnalysis): LogTone {
  if (flow.dominant_direction === "up" && flow.dominance_score >= 0.08) return "positive";
  if (flow.dominant_direction === "down" && flow.dominance_score <= -0.08) return "negative";
  return "warning";
}

function toneForPosition(position: OpenPosition): LogTone {
  const pnl = numberOr(position.unrealized_pnl);
  if (pnl > 0) return "positive";
  if (pnl < 0) return "negative";
  return "neutral";
}

function logToneClass(tone: LogTone) {
  switch (tone) {
    case "positive":
      return {
        badge: "bg-poly-green text-poly-black",
        border: "border-poly-green/40",
        panel: "bg-poly-green/5",
        text: "text-poly-green",
      };
    case "negative":
      return {
        badge: "bg-poly-red text-white",
        border: "border-poly-red/40",
        panel: "bg-poly-red/5",
        text: "text-poly-red",
      };
    case "warning":
      return {
        badge: "bg-poly-amber text-poly-black",
        border: "border-poly-amber/40",
        panel: "bg-poly-amber/5",
        text: "text-poly-amber",
      };
    case "neutral":
    default:
      return {
        badge: "bg-poly-cyan text-poly-black",
        border: "border-poly-cyan/30",
        panel: "bg-poly-cyan/5",
        text: "text-poly-cyan",
      };
  }
}

function signalLog(signal: Signal): LogLine {
  const edge = numberOr(signal.edge);
  const confidence = numberOr(signal.confidence);
  return {
    time: signal.created_at,
    tone: toneForSignal(signal),
    tag: "ANL",
    title: `${signal.asset_symbol} ${signalDirection(signal)} ${labelStrategy(signal.strategy_id)}`,
    detail: `edge ${edge.toFixed(3)} | conf ${asPercent(confidence)}${signal.regime ? ` | regime ${signal.regime}` : ""}`,
    meta: "analysis",
  };
}

function signalMetricsLog(signal: Signal): LogLine {
  const edge = numberOr(signal.edge);
  const confidence = numberOr(signal.confidence);
  const modelProbability = numberOr(signal.model_probability);
  const marketProbability = numberOr(signal.market_probability);
  const slippageBps = numberOr(signal.expected_slippage_bps);
  const holdingMinutes = numberOr(signal.expected_holding_minutes);
  const volume24h = numberOr(signal.volume_24h);
  return {
    time: signal.created_at,
    tone: toneForSignal(signal),
    tag: "SIG",
    title: `${signal.asset_symbol} ${signalDirection(signal)} ${labelStrategy(signal.strategy_id)}`,
    detail: `edge ${edge.toFixed(3)} | conf ${asPercent(confidence)} | model ${asPercent(modelProbability)} | market ${asPercent(marketProbability)}`,
    meta: `slip ${slippageBps.toFixed(0)}bps | hold ${holdingMinutes.toFixed(0)}m | vol ${asCurrency(volume24h)}${signal.regime ? ` | regime ${signal.regime}` : ""}`,
  };
}

function buildSignalMetricsLog(signals: Signal[], filter: SignalMetricsFilter): LogLine[] {
  return signals
    .filter((signal) => (filter === "all" ? true : signal.strategy_id === filter))
    .slice(0, 8)
    .map(signalMetricsLog)
    .sort((a, b) => b.time.localeCompare(a.time));
}

function buildStrategyLog(
  strategy: "pair_15m" | "momentum_15m" | "weather_copytrade",
  signals: Signal[],
  decisions: Decision[],
  orders: Order[],
  riskEvents: RiskEvent[],
) {
  const lines: LogLine[] = [];

  signals
    .filter((signal) => signal.strategy_id === strategy)
    .slice(0, 3)
    .forEach((signal) => lines.push(signalLog(signal)));

  decisions
    .filter((decision) => signals.some((signal) => signal.signal_id === decision.signal_id && signal.strategy_id === strategy))
    .slice(0, 2)
    .forEach((decision) => {
      lines.push({
        time: decision.created_at,
        tone: toneForDecision(decision),
        tag: "DEC",
        title: `${decision.asset_symbol} ${decision.approved ? "APPROVED" : "REJECTED"}`,
        detail: decision.notes,
        meta: `${decision.kelly_size ?? 0} units | risk ${asPercent(decision.risk_fraction ?? 0)}`,
      });
    });

  orders
    .filter((order) => order.strategy_id === strategy)
    .slice(0, 2)
    .forEach((order) => {
      lines.push({
        time: order.created_at,
        tone: toneForOrder(order),
        tag: "ORD",
        title: `${order.asset_symbol} ${order.direction} ${order.action ?? "entry"}`,
        detail: `realized ${asCurrencySigned(order.realized_pnl_usd ?? 0)}`,
        meta: order.status.toUpperCase(),
      });
    });

  riskEvents.slice(0, 2).forEach((event) => {
    lines.push({
      time: event.created_at,
      tone: toneForRiskEvent(event),
      tag: "RSK",
      title: `[${event.agent}] ${event.reason}`,
      detail: "risk pipeline",
    });
  });

  return lines.sort((a, b) => b.time.localeCompare(a.time)).slice(0, 6);
}

function buildOrderLog(orders: Order[], strategy?: string): LogLine[] {
  const filtered = strategy ? orders.filter((order) => order.strategy_id === strategy) : orders;
  return filtered.slice(0, 8).map((order) => {
    const status = order.status.toLowerCase();
    const tag = status === "live_filled" ? "FILL" : status === "live_submitted" ? "SUB" : status === "cancelled" ? "CAN" : "OPS";
    const notional = numberOr(order.notional_usd);
    const pnl = numberOr(order.realized_pnl_usd);
    return {
      time: order.created_at,
      tone: toneForOrder(order),
      tag,
      title: `${order.asset_symbol} ${order.direction} ${order.action ?? "entry"}${order.strategy_id ? ` · ${labelStrategy(order.strategy_id)}` : ""}`,
      detail: `${notional > 0 ? `notional ${asCurrency(notional)} | ` : ""}realized ${asCurrencySigned(pnl)}`,
      meta: `${order.status.toUpperCase()}${order.market_id ? ` | ${order.market_id}` : ""}${order.exchange_order_id ? ` | ${order.exchange_order_id.slice(0, 10)}` : ""}`,
    };
  });
}

function buildOperationsLog(orders: Order[]): LogLine[] {
  return buildOrderLog(orders);
}

function evaluateStrategyGoals(strategy: "pair_15m" | "momentum_15m", report: PerformanceReport | null): StrategyGoalStatus {
  const summary = report?.summary;
  const signals = numberOr(summary?.signals);
  const realized = numberOr(summary?.realized_pnl_window);
  const notional = numberOr(summary?.total_order_notional);
  const winRate = numberOr(summary?.win_rate);
  const maxDrawdown = numberOr(summary?.max_drawdown);
  const riskEvents = numberOr(summary?.risk_events);
  const riskLimit = strategy === "momentum_15m" ? 40 : 20;
  const riskPerSignal = signals > 0 ? riskEvents / signals : Number.POSITIVE_INFINITY;
  const pnlPerNotional = notional > 0 ? realized / notional : 0;

  const checks: StrategyGoalCheck[] = [
    { label: "PnL/Notional", value: asPercent(pnlPerNotional), target: ">= 1.0%", ok: pnlPerNotional >= 0.01 },
    { label: "Win%", value: asPercent(winRate), target: ">= 48.0%", ok: winRate >= 0.48 },
    { label: "Max DD", value: asPercent(maxDrawdown), target: "<= 3.0%", ok: maxDrawdown <= 0.03 },
    {
      label: "Risk/Signal",
      value: Number.isFinite(riskPerSignal) ? riskPerSignal.toFixed(1) : "-",
      target: strategy === "momentum_15m" ? "< 40.0" : "< 20.0",
      ok: Number.isFinite(riskPerSignal) && riskPerSignal < riskLimit,
    },
  ];

  const passed = checks.filter((check) => check.ok).length;
  return {
    strategy,
    subtitle:
      strategy === "pair_15m"
        ? "pairing discipline, symmetric legs, and edge quality"
        : "trend edge, noise control, and execution quality",
    go: passed === checks.length,
    score: `${passed}/${checks.length}`,
    checks,
  };
}

function positionHeadline(position: OpenPosition) {
  return `${position.asset_symbol} ${position.direction} ${labelStrategy(position.strategy_id)}`;
}

function positionDetail(position: OpenPosition) {
  return `size ${position.size} | avg ${asCurrency(position.average_price)} | mark ${asCurrency(position.current_price)} | spread ${numberOr(position.latest_spread_bps).toFixed(1)} bps`;
}

export function DashboardClient() {
  const [state, setState] = useState<DashboardState>({
    statuses: {},
    portfolio: null,
    liveBootstrap: null,
    overview: null,
    performance: null,
    pairPerformance: null,
    momentumPerformance: null,
    weatherCopytradeSummary: null,
    weatherCopytradeMetrics: null,
    signals: [],
    decisions: [],
    orders: [],
    positions: [],
    riskEvents: [],
    flowAnalyses: [],
  });
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState("booting");
  const [clock, setClock] = useState("--:--:--");
  const [signalMetricsFilter, setSignalMetricsFilter] = useState<SignalMetricsFilter>("all");
  const [weatherActionBusy, setWeatherActionBusy] = useState(false);
  const [weatherActionNote, setWeatherActionNote] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);
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
        { key: "liveBootstrap", path: "/live/bootstrap-status", fallback: current.liveBootstrap },
        { key: "positions", path: "/portfolio/positions", fallback: current.positions },
        { key: "overview", path: "/metrics/overview", fallback: current.overview },
        { key: "performance", path: "/metrics/performance?hours=24", fallback: current.performance },
        { key: "pairPerformance", path: "/metrics/performance?hours=336&strategy=pair_15m", fallback: current.pairPerformance },
        { key: "momentumPerformance", path: "/metrics/performance?hours=336&strategy=momentum_15m", fallback: current.momentumPerformance },
        { key: "weatherCopytradeSummary", path: "/weather-copytrade/summary?limit=12", fallback: current.weatherCopytradeSummary },
        { key: "weatherCopytradeMetrics", path: "/weather-copytrade/metrics?hours=720", fallback: current.weatherCopytradeMetrics },
        { key: "signals", path: "/signals/recent", fallback: current.signals },
        { key: "decisions", path: "/decisions/recent", fallback: current.decisions },
        { key: "orders", path: "/orders/recent", fallback: current.orders },
        { key: "riskEvents", path: "/risk-events/recent", fallback: current.riskEvents },
        { key: "flowAnalyses", path: "/analysis/flow/recent?limit=32", fallback: current.flowAnalyses },
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
        const normalizedWeatherSummary = normalizeWeatherCopytradeSummary(next.get("weatherCopytradeSummary"));
        setState({
          statuses: (next.get("statuses") ?? {}) as Record<string, AgentStatus>,
          portfolio: (next.get("portfolio") ?? null) as PortfolioSummary | null,
          liveBootstrap: (next.get("liveBootstrap") ?? null) as DashboardState["liveBootstrap"],
          overview: (next.get("overview") ?? null) as MetricsOverview | null,
          performance: (next.get("performance") ?? null) as PerformanceReport | null,
          pairPerformance: (next.get("pairPerformance") ?? null) as PerformanceReport | null,
          momentumPerformance: (next.get("momentumPerformance") ?? null) as PerformanceReport | null,
          weatherCopytradeSummary: normalizedWeatherSummary,
          weatherCopytradeMetrics: (next.get("weatherCopytradeMetrics") ?? null) as PerformanceReport | null,
          signals: (next.get("signals") ?? []) as Signal[],
          decisions: (next.get("decisions") ?? []) as Decision[],
          orders: (next.get("orders") ?? []) as Order[],
          positions: (next.get("positions") ?? []) as OpenPosition[],
          riskEvents: (next.get("riskEvents") ?? []) as RiskEvent[],
          flowAnalyses: (next.get("flowAnalyses") ?? []) as FlowAnalysis[],
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
  }, [refreshTick]);

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
  const orderLifecycle = state.performance?.order_lifecycle_summary ?? null;
  const operationsLog = buildOperationsLog(state.orders);
  const weatherOperationsLog = buildOrderLog(state.orders, "weather_copytrade");
  const pairLog = buildStrategyLog("pair_15m", state.signals, state.decisions, state.orders, state.riskEvents);
  const momentumLog = buildStrategyLog("momentum_15m", state.signals, state.decisions, state.orders, state.riskEvents);
  const signalMetrics = buildSignalMetricsLog(state.signals, signalMetricsFilter);
  const openPositions = [...state.positions].sort((a, b) => numberOr(b.unrealized_pnl) - numberOr(a.unrealized_pnl));
  const positivePositions = openPositions.filter((position) => numberOr(position.unrealized_pnl) > 0).length;
  const negativePositions = openPositions.filter((position) => numberOr(position.unrealized_pnl) < 0).length;
  const flowAnalyses = [...state.flowAnalyses].sort((a, b) =>
    (b.updated_at ?? b.created_at ?? b.last_trade_at ?? "").localeCompare(a.updated_at ?? a.created_at ?? a.last_trade_at ?? ""),
  );
  const latestFlow = flowAnalyses[0] ?? null;
  const flowUpNotional = flowAnalyses.reduce((sum, flow) => sum + numberOr(flow.up_notional), 0);
  const flowDownNotional = flowAnalyses.reduce((sum, flow) => sum + numberOr(flow.down_notional), 0);
  const flowDominanceAvg =
    flowAnalyses.length > 0 ? flowAnalyses.reduce((sum, flow) => sum + numberOr(flow.dominance_score), 0) / flowAnalyses.length : 0;
  const flowChartData = flowAnalyses.slice(0, 24).reverse().map((flow) => ({
    ...flow,
    created_at: flow.updated_at ?? flow.created_at ?? flow.last_trade_at ?? new Date().toISOString(),
  }));
  const liveOrders = numberOr(summary?.live_orders);
  const paperOrders = numberOr(summary?.paper_orders);
  const liveSubmittedOrders = numberOr(summary?.live_submitted_orders);
  const liveFilledOrders = numberOr(summary?.live_filled_orders);
  const liveFailOpen = Boolean(state.liveBootstrap?.fail_open);
  const weatherSummary = state.weatherCopytradeSummary;
  const weatherRun = weatherSummary?.run ?? null;
  const weatherCandidates = [...(weatherSummary?.candidates ?? [])].sort((a, b) => a.rank - b.rank || b.score - a.score);
  const weatherState = weatherSummary?.state ?? null;
  const weatherSelected =
    weatherCandidates.find((candidate) => candidate.proxy_wallet === weatherState?.selected_proxy_wallet) ??
    weatherCandidates.find((candidate) => candidate.selected) ??
    (weatherCandidates.length > 0 ? weatherCandidates[0] : null);
  const weatherReport = weatherSummary?.report ?? weatherState?.report ?? weatherRun?.model_summary ?? null;
  const weatherMetrics = state.weatherCopytradeMetrics ?? null;
  const weatherCopyTradeFraction = optionalNumber(
    normalizeRecord(weatherRun?.metadata)?.copy_trade_fraction ?? normalizeRecord(weatherState?.metadata)?.copy_trade_fraction,
  );
  const weatherBusyLabel = weatherActionBusy ? "WORKING" : weatherState?.active ? "ACTIVE" : weatherState?.paused ? "PAUSED" : "IDLE";

  async function refreshWeatherCopytrade() {
    setWeatherActionNote(null);
    setRefreshTick((value) => value + 1);
  }

  async function handleWeatherAction(
    action: "run" | "approve" | "pause" | "resume",
    payload: Record<string, unknown> = {},
  ) {
    setWeatherActionBusy(true);
    setWeatherActionNote(null);
    try {
      if (action === "run") {
        await postJson("/weather-copytrade/run", { limit: 40, ...payload });
        setWeatherActionNote("Nova analise executada com sucesso.");
      } else if (action === "approve") {
        await postJson("/weather-copytrade/approve", payload);
        setWeatherActionNote("Usuario aprovado e configurado para copytrade.");
      } else if (action === "pause") {
        await postJson("/weather-copytrade/pause", { paused: true });
        setWeatherActionNote("Copytrade pausado.");
      } else if (action === "resume") {
        await postJson("/weather-copytrade/pause", { paused: false });
        setWeatherActionNote("Copytrade reativado.");
      }
      await refreshWeatherCopytrade();
    } catch (error) {
      setWeatherActionNote(error instanceof Error ? error.message : "Falha ao executar acao.");
    } finally {
      setWeatherActionBusy(false);
    }
  }

  return (
    <main className="min-h-screen overflow-y-auto overflow-x-hidden px-4 pb-16 pt-4 md:px-6 md:pb-20 md:pt-6 custom-scrollbar">
      <div className="mx-auto flex max-w-7xl flex-col gap-4">
        <section className="border border-poly-border bg-[radial-gradient(circle_at_top,rgba(0,243,255,0.08),transparent_28%),radial-gradient(circle_at_bottom,rgba(0,255,65,0.06),transparent_24%),linear-gradient(180deg,rgba(255,255,255,0.01),transparent)] p-4 md:p-5">
          <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-poly-dim">POLYTERM_V1.04</div>
              <div className="mt-2 flex items-center gap-2 font-mono text-sm">
                <span className={`h-2 w-2 rounded-full ${runningAgents > 0 ? "bg-poly-green animate-pulse-dot" : "bg-poly-red"}`} />
                <span className={runningAgents > 0 ? "text-poly-green glow-green" : "text-poly-red glow-red"}>
                  {runningAgents > 0 ? "ONLINE" : "DEGRADED"}
                </span>
                {state.liveBootstrap?.mode === "live" ? (
                  <span className={`border px-2 py-0.5 text-[10px] uppercase ${liveFailOpen ? "border-poly-amber text-poly-amber" : "border-poly-cyan text-poly-cyan"}`}>
                    {liveFailOpen ? "LIVE_FAIL_OPEN" : "LIVE"}
                  </span>
                ) : null}
              </div>
              <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                <Metric label="Balance" value={asCurrency(balance)} tone="text-poly-green" />
                <Metric label="PnL" value={asCurrencySigned(pnl)} tone={pnl >= 0 ? "text-poly-green" : "text-poly-red"} />
                <Metric label="Realized" value={asCurrencySigned(realized)} tone={realized >= 0 ? "text-poly-cyan" : "text-poly-red"} />
                <Metric label="Unrealized" value={asCurrencySigned(unrealized)} tone={unrealized >= 0 ? "text-poly-amber" : "text-poly-red"} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 text-right font-mono text-[10px] uppercase text-poly-dim md:min-w-[330px]">
              <div className="border border-poly-border px-3 py-2">
                <div>Win%</div>
                <div className="text-sm normal-case text-poly-cyan">{summary?.win_rate != null ? asPercent(summary.win_rate) : "-"}</div>
              </div>
              <div className="border border-poly-border px-3 py-2">
                <div>Equity</div>
                <div className="text-sm normal-case text-poly-cyan">{asCurrency(equity)}</div>
              </div>
              <div className="border border-poly-border px-3 py-2">
                <div>Agents</div>
                <div className="text-sm normal-case text-poly-cyan">{runningAgents}/{totalAgents}</div>
              </div>
              <div className="border border-poly-border px-3 py-2">
                <div>Updated</div>
                <div className="text-sm normal-case text-poly-cyan">{error ? "API_ERROR" : lastUpdated}</div>
              </div>
            </div>
          </div>
          {state.liveBootstrap?.mode === "live" ? (
            <div className={`mt-4 border px-3 py-2 font-mono text-[10px] uppercase ${liveFailOpen ? "border-poly-amber text-poly-amber" : "border-poly-cyan text-poly-cyan"}`}>
              bootstrap {state.liveBootstrap.ready ? "ready" : "degraded"} | {state.liveBootstrap.reason ?? "live mode"}
            </div>
          ) : null}
        </section>

        <section className="grid gap-4 lg:grid-cols-2">
          <StrategyGoalCard goal={pairGoal} />
          <StrategyGoalCard goal={momentumGoal} />
        </section>

        <section className="border border-poly-border bg-poly-black p-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.25em] text-poly-dim">ORDER_LIFECYCLE</div>
              <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.2em] text-poly-muted">
                execution latency, open duration, and cancellation pressure from the live ledger
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 text-right font-mono text-[9px] uppercase text-poly-dim sm:min-w-[360px] sm:grid-cols-4">
              <div className="border border-poly-border px-3 py-2">
                <div>Fill Rate</div>
                <div className="text-sm normal-case text-poly-cyan">
                  {orderLifecycle ? asPercent(orderLifecycle.fill_rate) : "-"}
                </div>
              </div>
              <div className="border border-poly-border px-3 py-2">
                <div>Cancel Rate</div>
                <div className="text-sm normal-case text-poly-cyan">
                  {orderLifecycle ? asPercent(orderLifecycle.cancel_rate) : "-"}
                </div>
              </div>
              <div className="border border-poly-border px-3 py-2">
                <div>Fill Latency</div>
                <div className="text-sm normal-case text-poly-cyan">
                  {orderLifecycle ? `${Math.round(orderLifecycle.avg_fill_latency_seconds)}s` : "-"}
                </div>
              </div>
              <div className="border border-poly-border px-3 py-2">
                <div>Open Time</div>
                <div className="text-sm normal-case text-poly-cyan">
                  {orderLifecycle ? `${Math.round(orderLifecycle.avg_open_duration_seconds)}s` : "-"}
                </div>
              </div>
            </div>
          </div>
          <div className="mt-4 grid gap-3 lg:grid-cols-[1.2fr_0.8fr]">
            <div className="border border-poly-border bg-poly-surface-dim/20 p-3 font-mono text-[10px] text-poly-text">
              <div className="uppercase tracking-[0.2em] text-poly-dim">Lifecycle Notes</div>
              <div className="mt-2 space-y-1 text-poly-muted">
                <div>Tracked orders: {orderLifecycle?.tracked_orders ?? "-"}</div>
                <div>Live submitted: {orderLifecycle?.live_submitted_orders ?? "-"}</div>
                <div>Live filled: {orderLifecycle?.live_filled_orders ?? "-"}</div>
                <div>Pending cancelled: {orderLifecycle?.pending_cancelled_orders ?? "-"}</div>
              </div>
            </div>
            <div className="border border-poly-border bg-poly-surface-dim/20 p-3 font-mono text-[10px] text-poly-text">
              <div className="uppercase tracking-[0.2em] text-poly-dim">Cancel Reasons</div>
              <div className="mt-2 space-y-1 text-poly-muted">
                {orderLifecycle?.cancel_reason_breakdown?.length ? (
                  orderLifecycle.cancel_reason_breakdown.slice(0, 4).map((item) => (
                    <div key={item.label} className="flex items-center justify-between gap-3">
                      <span className="truncate">{item.label}</span>
                      <span className="text-poly-cyan">{item.count}</span>
                    </div>
                  ))
                ) : (
                  <div>no cancellation data yet</div>
                )}
              </div>
            </div>
          </div>
        </section>

        <section className="border border-poly-border bg-poly-black p-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.25em] text-poly-dim">FLOW_15M_SIGNAL</div>
              <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.2em] text-poly-muted">
                pressure balance, trade imbalance, and confidence overlay for the next review pass
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 text-right font-mono text-[9px] uppercase text-poly-dim sm:min-w-[340px] sm:grid-cols-4">
              <div className="border border-poly-border px-3 py-2">
                <div>Latest</div>
                <div className={`text-sm normal-case ${latestFlow ? logToneClass(toneForFlow(latestFlow)).text : "text-poly-cyan"}`}>
                  {latestFlow ? flowDirectionLabel(latestFlow) : "N/A"}
                </div>
              </div>
              <div className="border border-poly-border px-3 py-2">
                <div>Dominance</div>
                <div className="text-sm normal-case text-poly-cyan">{asPercent(flowDominanceAvg)}</div>
              </div>
              <div className="border border-poly-border px-3 py-2">
                <div>Up / Down</div>
                <div className="text-sm normal-case text-poly-cyan">
                  {asCurrency(flowUpNotional)} / {asCurrency(flowDownNotional)}
                </div>
              </div>
              <div className="border border-poly-border px-3 py-2">
                <div>Confidence</div>
                <div className="text-sm normal-case text-poly-cyan">{latestFlow ? asPercent(latestFlow.confidence) : "-"}</div>
              </div>
            </div>
          </div>

          <div className="mt-4 grid gap-4 xl:grid-cols-[1.35fr_0.85fr]">
            <FlowAnalysisChart data={flowChartData} />
            <div className="grid gap-3">
              {latestFlow ? (
                <>
                  <div className="border border-poly-border bg-poly-surface-dim/20 p-3">
                    <div className="font-mono text-[9px] uppercase tracking-[0.2em] text-poly-dim">Current Bias</div>
                    <div className={`mt-1 font-mono text-xl font-bold ${logToneClass(toneForFlow(latestFlow)).text}`}>
                      {flowDirectionLabel(latestFlow)}
                    </div>
                    <div className="mt-1 font-mono text-[9px] text-poly-muted">
                      {latestFlow.asset_symbol}
                      {latestFlow.cycle_slug ? ` | ${latestFlow.cycle_slug}` : ""}
                    </div>
                  </div>
                  <div className="border border-poly-border bg-poly-surface-dim/20 p-3">
                    <div className="font-mono text-[9px] uppercase tracking-[0.2em] text-poly-dim">Window Stats</div>
                    <div className="mt-2 grid grid-cols-2 gap-2 font-mono text-[9px]">
                      <div className="border border-poly-border px-2 py-2">
                        <div className="text-poly-dim uppercase">Trades</div>
                        <div className="text-sm text-poly-cyan">{latestFlow.total_trades}</div>
                      </div>
                      <div className="border border-poly-border px-2 py-2">
                        <div className="text-poly-dim uppercase">Freshness</div>
                        <div className="text-sm text-poly-cyan">{Math.round(latestFlow.freshness_seconds)}s</div>
                      </div>
                      <div className="border border-poly-border px-2 py-2">
                        <div className="text-poly-dim uppercase">Up Count</div>
                        <div className="text-sm text-poly-green">{latestFlow.up_trade_count}</div>
                      </div>
                      <div className="border border-poly-border px-2 py-2">
                        <div className="text-poly-dim uppercase">Down Count</div>
                        <div className="text-sm text-poly-red">{latestFlow.down_trade_count}</div>
                      </div>
                    </div>
                  </div>
                  <div className="border border-poly-border bg-poly-surface-dim/20 p-3 font-mono text-[9px] text-poly-dim">
                    <div className="uppercase tracking-[0.2em]">Source</div>
                    <div className="mt-1 text-poly-muted">
                      {latestFlow.source_used ?? "ws"}
                      {latestFlow.sample_count ? ` | samples ${latestFlow.sample_count}` : ""}
                      {latestFlow.metadata?.["aligned_with_signal"] === true ? " | aligned" : ""}
                    </div>
                  </div>
                </>
              ) : (
                <div className="border border-dashed border-poly-border p-4 font-mono text-[10px] text-poly-dim">
                  waiting for flow analysis snapshots
                </div>
              )}
            </div>
          </div>
        </section>

        <section className="grid gap-4 xl:grid-cols-[1.12fr_1fr_1fr]">
          <OpenPositionsPanel
            positions={openPositions}
            positivePositions={positivePositions}
            negativePositions={negativePositions}
          />
          <LogPanel title="Pair_15M_Log" items={pairLog} />
          <LogPanel title="Momentum_15M_Log" items={momentumLog} />
        </section>

        <section className="grid gap-4 xl:grid-cols-[1.12fr_1fr_1fr]">
          <LogPanel title="Operations_Log" items={operationsLog} />
          <LogPanel
            title="Weather_Copytrade_Log"
            items={weatherOperationsLog}
          />
          <div className="border border-poly-border bg-poly-black p-4 font-mono text-[10px] text-poly-dim">
            <div className="uppercase tracking-[0.25em] text-poly-dim">Operational Notes</div>
            <div className="mt-3 space-y-2 text-poly-text">
              <div>Orders are now shown from the local ledger in the same format used by the metrics engine.</div>
              <div>Live orders are synced back into the ledger so fills and cancellations can surface after restarts.</div>
              <div>Weather copytrade operations now have a dedicated log slice for easier inspection.</div>
            </div>
          </div>
        </section>

        <section className="border border-poly-border bg-[radial-gradient(circle_at_top_left,rgba(0,243,255,0.10),transparent_30%),radial-gradient(circle_at_bottom_right,rgba(0,255,65,0.08),transparent_28%),linear-gradient(180deg,rgba(255,255,255,0.01),transparent)] p-4 md:p-5">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-poly-dim">WEATHER_COPYTRADE</div>
              <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.18em] text-poly-muted">
                scan conservador de traders PUBLICOS, shortlist deterministica, e ativacao manual antes de copiar
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 font-mono text-[9px] uppercase text-poly-dim">
              <span className={`border px-2 py-1 ${weatherBusyLabel === "ACTIVE" ? "border-poly-green text-poly-green" : weatherBusyLabel === "PAUSED" ? "border-poly-amber text-poly-amber" : "border-poly-cyan text-poly-cyan"}`}>
                {weatherBusyLabel}
              </span>
              <button
                type="button"
                disabled={weatherActionBusy}
                onClick={() => void handleWeatherAction("run")}
                className="border border-poly-cyan px-3 py-1 text-poly-cyan transition hover:bg-poly-cyan hover:text-poly-black disabled:cursor-not-allowed disabled:opacity-50"
              >
                Nova análise
              </button>
              <button
                type="button"
                disabled={weatherActionBusy || !weatherSelected}
                onClick={() =>
                  void handleWeatherAction("approve", {
                    run_id: weatherRun?.run_id,
                    proxy_wallet: weatherSelected?.proxy_wallet,
                  })
                }
                className="border border-poly-green px-3 py-1 text-poly-green transition hover:bg-poly-green hover:text-poly-black disabled:cursor-not-allowed disabled:opacity-50"
              >
                Aprovar e ativar
              </button>
              <button
                type="button"
                disabled={weatherActionBusy || !weatherState}
                onClick={() => void handleWeatherAction(weatherState?.paused ? "resume" : "pause")}
                className="border border-poly-amber px-3 py-1 text-poly-amber transition hover:bg-poly-amber hover:text-poly-black disabled:cursor-not-allowed disabled:opacity-50"
              >
                {weatherState?.paused ? "Retomar" : "Pausar"}
              </button>
            </div>
          </div>

          <div className="mt-4 grid gap-4 xl:grid-cols-[1.1fr_0.95fr_0.95fr]">
            <div className="border border-poly-border bg-poly-black p-4">
              <div className="flex items-center justify-between gap-3 font-mono text-[10px] uppercase tracking-[0.2em] text-poly-dim">
                <span>Última análise</span>
                <span className="text-poly-cyan">{weatherRun ? weatherRun.run_id.slice(0, 8) : "sem_run"}</span>
              </div>
              <div className="mt-3 grid gap-2 text-[10px] font-mono text-poly-dim sm:grid-cols-2">
                <div className="border border-poly-border px-3 py-2">
                  <div className="uppercase">Universo</div>
                  <div className="mt-1 text-poly-cyan">{weatherRun ? weatherRun.universe_count : "-"}</div>
                </div>
                <div className="border border-poly-border px-3 py-2">
                  <div className="uppercase">Shortlist</div>
                  <div className="mt-1 text-poly-cyan">{weatherRun ? weatherRun.shortlisted_count : "-"}</div>
                </div>
                <div className="border border-poly-border px-3 py-2">
                  <div className="uppercase">Selecionado</div>
                  <div className="mt-1 text-poly-green">{weatherRun?.selected_user_name ?? weatherState?.selected_user_name ?? "-"}</div>
                </div>
                <div className="border border-poly-border px-3 py-2">
                  <div className="uppercase">Model</div>
                  <div className="mt-1 text-poly-cyan">{weatherReport?.model ?? "deterministic"}</div>
                </div>
              </div>
              <div className="mt-3 border border-poly-border bg-poly-surface-dim/20 p-3">
                <div className="font-mono text-[9px] uppercase tracking-[0.2em] text-poly-dim">Resumo do modelo</div>
                <div className="mt-2 space-y-2 font-mono text-[10px] text-poly-text">
                  <div>
                    <span className="text-poly-dim">summary:</span> {weatherReport?.summary ?? "sem resumo disponivel"}
                  </div>
                  <div>
                    <span className="text-poly-dim">why:</span> {weatherReport?.why ?? "sem justificativa ainda"}
                  </div>
                  <div>
                    <span className="text-poly-dim">risks:</span>{" "}
                    {weatherReport?.risks?.length ? weatherReport.risks.join(" | ") : "sem riscos registrados"}
                  </div>
                  <div>
                    <span className="text-poly-dim">reason:</span> {weatherReport?.selection_reason ?? "sem reason"}
                  </div>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2 font-mono text-[9px] uppercase text-poly-dim">
                <span className="border border-poly-border px-2 py-1">
                  Approved: <span className={weatherState?.approved ? "text-poly-green" : "text-poly-red"}>{String(Boolean(weatherState?.approved))}</span>
                </span>
                <span className="border border-poly-border px-2 py-1">
                  Active: <span className={weatherState?.active ? "text-poly-green" : "text-poly-red"}>{String(Boolean(weatherState?.active))}</span>
                </span>
                <span className="border border-poly-border px-2 py-1">
                  Paused: <span className={weatherState?.paused ? "text-poly-amber" : "text-poly-green"}>{String(Boolean(weatherState?.paused))}</span>
                </span>
              </div>
              {weatherActionNote ? (
                <div className="mt-3 border border-poly-amber/40 bg-poly-amber/5 px-3 py-2 font-mono text-[10px] text-poly-amber">
                  {weatherActionNote}
                </div>
              ) : null}
            </div>

            <div className="border border-poly-border bg-poly-black p-4">
              <div className="flex items-center justify-between gap-3 font-mono text-[10px] uppercase tracking-[0.2em] text-poly-dim">
                <span>Candidatos</span>
                <span className="text-poly-cyan">{weatherCandidates.length}</span>
              </div>
              <div className="mt-3 space-y-2">
                {weatherCandidates.length > 0 ? (
                  weatherCandidates.slice(0, 6).map((candidate) => {
                    const isSelected = candidate.proxy_wallet === weatherState?.selected_proxy_wallet || candidate.selected;
                    const metrics = normalizeRecord(candidate.metrics) ?? {};
                    const pnl30d = optionalNumber(metrics.pnl_30d ?? metrics.pnl30d);
                    const maxDrawdown = optionalNumber(metrics.max_drawdown ?? metrics.maxDrawdown);
                    const profitFactor = optionalNumber(metrics.profit_factor ?? metrics.profitFactor);
                    const trades30d = optionalNumber(metrics.trades_30d ?? metrics.trades30d);
                    return (
                      <div
                        key={`${candidate.proxy_wallet}-${candidate.rank}`}
                        className={`border px-3 py-2 ${isSelected ? "border-poly-green bg-poly-green/5" : "border-poly-border bg-poly-surface-dim/20"}`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-poly-dim">
                              #{candidate.rank} {candidate.user_name}
                            </div>
                            <div className="mt-1 break-all font-mono text-[9px] text-poly-muted">{candidate.proxy_wallet}</div>
                          </div>
                          <div className="text-right font-mono text-[10px]">
                            <div className={isSelected ? "text-poly-green" : "text-poly-cyan"}>{candidate.score.toFixed(2)}</div>
                            <div className="text-[9px] uppercase text-poly-dim">{isSelected ? "selected" : candidate.passed === false ? "rejected" : "candidate"}</div>
                          </div>
                        </div>
                        <div className="mt-2 grid gap-1 font-mono text-[9px] text-poly-dim">
                          <div>{candidate.rationale}</div>
                          <div className="flex flex-wrap gap-2">
                            <span>pnl30d {pnl30d != null ? pnl30d.toFixed(2) : "-"}</span>
                            <span>dd {maxDrawdown != null ? asPercent(maxDrawdown) : "-"}</span>
                            <span>pf {profitFactor != null ? profitFactor.toFixed(2) : "-"}</span>
                            <span>trades {trades30d != null ? trades30d : "-"}</span>
                          </div>
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <div className="border border-dashed border-poly-border px-3 py-4 text-center font-mono text-[10px] text-poly-dim">
                    aguarde uma analise para preencher a shortlist
                  </div>
                )}
              </div>
            </div>

            <div className="border border-poly-border bg-poly-black p-4">
              <div className="flex items-center justify-between gap-3 font-mono text-[10px] uppercase tracking-[0.2em] text-poly-dim">
                <span>Operação copiada</span>
                <span className="text-poly-cyan">{weatherMetrics ? "live" : "sem_metricas"}</span>
              </div>
              <div className="mt-3 grid gap-2 text-[10px] font-mono text-poly-dim sm:grid-cols-2">
                <div className="border border-poly-border px-3 py-2">
                  <div className="uppercase">Orders</div>
                  <div className="mt-1 text-poly-cyan">{weatherMetrics?.summary.orders != null ? numberOr(weatherMetrics.summary.orders) : "-"}</div>
                </div>
                <div className="border border-poly-border px-3 py-2">
                  <div className="uppercase">Signals</div>
                  <div className="mt-1 text-poly-cyan">{weatherMetrics?.summary.signals != null ? numberOr(weatherMetrics.summary.signals) : "-"}</div>
                </div>
                <div className="border border-poly-border px-3 py-2">
                  <div className="uppercase">Execução</div>
                  <div className="mt-1 text-poly-green">
                    {weatherMetrics?.summary.execution_rate != null ? asPercent(numberOr(weatherMetrics.summary.execution_rate)) : "-"}
                  </div>
                </div>
                <div className="border border-poly-border px-3 py-2">
                  <div className="uppercase">Win%</div>
                  <div className="mt-1 text-poly-cyan">{weatherMetrics?.summary.win_rate != null ? asPercent(numberOr(weatherMetrics.summary.win_rate)) : "-"}</div>
                </div>
                <div className="border border-poly-border px-3 py-2">
                  <div className="uppercase">PnL</div>
                  <div className={`mt-1 ${numberOr(weatherMetrics?.summary.realized_pnl_window) >= 0 ? "text-poly-green" : "text-poly-red"}`}>
                    {weatherMetrics?.summary.realized_pnl_window != null ? asCurrencySigned(numberOr(weatherMetrics.summary.realized_pnl_window)) : "-"}
                  </div>
                </div>
                <div className="border border-poly-border px-3 py-2">
                  <div className="uppercase">DD</div>
                  <div className="mt-1 text-poly-amber">{weatherMetrics?.summary.max_drawdown != null ? asPercent(numberOr(weatherMetrics.summary.max_drawdown)) : "-"}</div>
                </div>
                <div className="border border-poly-border px-3 py-2">
                  <div className="uppercase">Risk</div>
                  <div className="mt-1 text-poly-red">{weatherMetrics?.summary.risk_events != null ? numberOr(weatherMetrics.summary.risk_events) : "-"}</div>
                </div>
                <div className="border border-poly-border px-3 py-2">
                  <div className="uppercase">Approval</div>
                  <div className="mt-1 text-poly-cyan">{weatherMetrics?.summary.approval_rate != null ? asPercent(numberOr(weatherMetrics.summary.approval_rate)) : "-"}</div>
                </div>
              </div>
              <div className="mt-3 border border-poly-border bg-poly-surface-dim/20 p-3 font-mono text-[10px] text-poly-dim">
                <div className="uppercase tracking-[0.2em]">Selecionado</div>
                <div className="mt-2 text-poly-text">
                  {weatherSelected ? `${weatherSelected.user_name} · ${weatherSelected.proxy_wallet}` : "nenhum usuario selecionado"}
                </div>
                <div className="mt-1 text-[9px] uppercase text-poly-muted">
                  copy_trade_fraction {weatherCopyTradeFraction != null ? asPercent(weatherCopyTradeFraction) : "-"}
                </div>
              </div>
            </div>
          </div>
        </section>

        <section>
          <div className="mb-2 flex flex-wrap items-center gap-2 font-mono text-[9px] uppercase text-poly-dim">
            <span className="tracking-[0.18em]">Signals Metrics Filter</span>
            <button
              type="button"
              onClick={() => setSignalMetricsFilter("all")}
              className={`border px-2 py-1 ${signalMetricsFilter === "all" ? "border-poly-cyan text-poly-cyan" : "border-poly-border text-poly-dim"}`}
            >
              all
            </button>
            <button
              type="button"
              onClick={() => setSignalMetricsFilter("pair_15m")}
              className={`border px-2 py-1 ${signalMetricsFilter === "pair_15m" ? "border-poly-cyan text-poly-cyan" : "border-poly-border text-poly-dim"}`}
            >
              pair_15m
            </button>
            <button
              type="button"
              onClick={() => setSignalMetricsFilter("momentum_15m")}
              className={`border px-2 py-1 ${signalMetricsFilter === "momentum_15m" ? "border-poly-cyan text-poly-cyan" : "border-poly-border text-poly-dim"}`}
            >
              momentum_15m
            </button>
          </div>
          <LogPanel title={`Signals_Metrics_Log_${signalMetricsFilter.toUpperCase()}`} items={signalMetrics} />
        </section>

        <section className="border border-poly-border bg-poly-black p-4 font-mono text-[10px] text-poly-dim">
          <div className="flex flex-wrap gap-4">
            <span>
              Orders: <span className="text-poly-cyan">{summary?.orders ?? 0}</span>
            </span>
            <span>
              Live: <span className="text-poly-amber">{liveOrders}</span>
            </span>
            <span>
              Paper: <span className="text-poly-cyan">{paperOrders}</span>
            </span>
            <span>
              Live submitted: <span className="text-poly-amber">{liveSubmittedOrders}</span>
            </span>
            <span>
              Live filled: <span className="text-poly-green">{liveFilledOrders}</span>
            </span>
            <span>
              Signals: <span className="text-poly-green">{summary?.signals ?? 0}</span>
            </span>
            <span>
              Risk: <span className="text-poly-red">{summary?.risk_events ?? 0}</span>
            </span>
            <span>
              Open: <span className="text-poly-amber">{state.portfolio?.open_positions ?? 0}</span>
            </span>
            <span>
              Risk/Signal:{" "}
              <span className="text-poly-cyan">{summary && summary.signals > 0 ? (summary.risk_events / summary.signals).toFixed(1) : "-"}</span>
            </span>
          </div>
        </section>
      </div>

      <div className="pointer-events-none fixed inset-0 crt-overlay" />
      <div className="fixed bottom-0 left-0 right-0 border-t border-poly-border bg-poly-surface-dim/90 px-4 py-1 font-mono text-[9px] text-poly-dim">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <span>GO focus: balance, pnl, win%, strategy gates, open operations</span>
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
          <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.2em] text-poly-muted">{goal.subtitle}</div>
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
              <div>
                <div className="font-mono text-sm text-poly-text">{check.value}</div>
                <div className="font-mono text-[8px] uppercase text-poly-dim">meta {check.target}</div>
              </div>
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

function OpenPositionsPanel({
  positions,
  positivePositions,
  negativePositions,
}: {
  positions: OpenPosition[];
  positivePositions: number;
  negativePositions: number;
}) {
  const totalPnL = positions.reduce((sum, position) => sum + numberOr(position.unrealized_pnl), 0);

  return (
    <section className="border border-poly-border bg-poly-black p-4">
      <div className="flex items-center justify-between gap-3 font-mono text-[10px] uppercase tracking-[0.25em] text-poly-dim">
        <span>OPEN_OPERATIONS</span>
        <span className="text-poly-cyan">{positions.length}</span>
      </div>
      <div className="mt-2 flex flex-wrap gap-3 font-mono text-[9px] text-poly-dim">
        <span>
          Positive: <span className="text-poly-green">{positivePositions}</span>
        </span>
        <span>
          Negative: <span className="text-poly-red">{negativePositions}</span>
        </span>
        <span>
          Unrealized: <span className={totalPnL >= 0 ? "text-poly-green" : "text-poly-red"}>{asCurrencySigned(totalPnL)}</span>
        </span>
      </div>
      <div className="mt-3 space-y-2">
        {positions.length > 0 ? (
          positions.slice(0, 7).map((position) => {
            const tone = logToneClass(toneForPosition(position));
            return (
              <div key={position.position_key} className={`border ${tone.border} ${tone.panel} px-3 py-2`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-mono text-[10px] uppercase tracking-[0.15em] text-poly-dim">{positionHeadline(position)}</div>
                    <div className="mt-1 font-mono text-[9px] text-poly-muted">{position.market_question}</div>
                  </div>
                  <div className={`font-mono text-[10px] font-bold ${tone.text}`}>{asCurrencySigned(position.unrealized_pnl)}</div>
                </div>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[9px] text-poly-dim">
                  <span>{positionDetail(position)}</span>
                  <span>value {asCurrency(position.current_value_usd)}</span>
                  <span>opened {new Date(position.opened_at).toLocaleTimeString()}</span>
                </div>
              </div>
            );
          })
        ) : (
          <div className="border border-poly-border px-3 py-4 text-center font-mono text-[10px] text-poly-dim">NO_OPEN_POSITIONS</div>
        )}
      </div>
    </section>
  );
}

function LogPanel({ title, items }: { title: string; items: LogLine[] }) {
  const positive = items.filter((item) => item.tone === "positive").length;
  const negative = items.filter((item) => item.tone === "negative").length;
  const warning = items.filter((item) => item.tone === "warning").length;

  return (
    <section className="border border-poly-border bg-poly-black p-4">
      <div className="flex items-center justify-between gap-3 font-mono text-[10px] uppercase tracking-[0.25em] text-poly-dim">
        <span>{title}</span>
        <span className="text-poly-cyan">{items.length}</span>
      </div>
      <div className="mt-2 flex flex-wrap gap-3 font-mono text-[9px] text-poly-dim">
        <span>
          + <span className="text-poly-green">{positive}</span>
        </span>
        <span>
          - <span className="text-poly-red">{negative}</span>
        </span>
        <span>
          ~ <span className="text-poly-amber">{warning}</span>
        </span>
      </div>
      <div className="mt-3 space-y-2">
        {items.length > 0 ? (
          items.map((item, index) => {
            const tone = logToneClass(item.tone);
            return (
              <div key={`${title}-${index}-${item.time}`} className={`border ${tone.border} ${tone.panel} px-3 py-2`}>
                <div className="flex items-center justify-between gap-3 font-mono text-[9px] text-poly-dim">
                  <span>{new Date(item.time).toLocaleTimeString()}</span>
                  <span className={`px-2 py-0.5 ${tone.badge}`}>{item.tag}</span>
                </div>
                <div className={`mt-1 font-mono text-[11px] ${tone.text}`}>{item.title}</div>
                {item.detail ? <div className="mt-1 font-mono text-[10px] text-poly-text">{item.detail}</div> : null}
                {item.meta ? <div className="mt-1 font-mono text-[9px] uppercase text-poly-dim">{item.meta}</div> : null}
              </div>
            );
          })
        ) : (
          <div className="border border-poly-border px-3 py-4 text-center font-mono text-[10px] text-poly-dim">NO_LOGS</div>
        )}
      </div>
    </section>
  );
}
