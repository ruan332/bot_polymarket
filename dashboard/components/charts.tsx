"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ResponsiveContainer,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type EquityPoint = { created_at: string; total_equity: number; total_pnl: number; unrealized_pnl: number };
type PipelinePoint = { bucket: string; signals: number; decisions: number; orders: number; risk_events: number };
type CostPoint = { agent: string; cost_usd: number; calls: number };
type BreakdownPoint = { label: string; count: number };
type FunnelStagePoint = { label: string; count: number; detail?: string; accent?: string };
type FlowPoint = {
  created_at: string;
  dominance_score: number;
  confidence: number;
  up_notional: number;
  down_notional: number;
  total_notional: number;
  dominant_direction: "up" | "down" | "neutral";
  asset_symbol?: string;
  cycle_slug?: string;
};

const C = {
  green: "#00ff41",
  cyan: "#00f3ff",
  amber: "#fbbf24",
  red: "#ff3131",
  dim: "#52525b",
  border: "#27272a",
  muted: "#a1a1aa",
};

const tipStyle = {
  backgroundColor: "rgba(0,0,0,0.95)",
  border: `1px solid ${C.border}`,
  borderRadius: "0px",
  color: "#e2e2e2",
  fontFamily: "'JetBrains Mono', monospace",
  fontSize: "10px",
};

const axisProps = {
  stroke: C.dim,
  tick: { fontSize: 9, fontFamily: "'JetBrains Mono', monospace", fill: C.dim },
  axisLine: { stroke: C.border },
  tickLine: { stroke: C.border },
};

export function EquityChart({ equity }: { equity: EquityPoint[] }) {
  return (
    <div className="chart-wrap-xl">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={equity} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
          <defs>
            <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={C.green} stopOpacity={0.25} />
              <stop offset="95%" stopColor={C.green} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={C.border} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="created_at"
            {...axisProps}
            tickFormatter={(v: string) => new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            minTickGap={40}
          />
          <YAxis {...axisProps} />
          <Tooltip
            labelFormatter={(v: string) => new Date(v).toLocaleString()}
            formatter={(value: number, name: string) => [`$${value.toFixed(2)}`, name]}
            contentStyle={tipStyle}
          />
          <Area type="monotone" dataKey="total_equity" stroke={C.green} fill="url(#equityFill)" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="total_pnl" stroke={C.cyan} strokeWidth={1.5} dot={false} />
          <Line type="monotone" dataKey="unrealized_pnl" stroke={C.amber} strokeWidth={1.5} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function PipelineChart({ data }: { data: PipelinePoint[] }) {
  return (
    <div className="chart-wrap-xl">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid stroke={C.border} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="bucket"
            {...axisProps}
            tickFormatter={(v: string) => new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            minTickGap={30}
          />
          <YAxis {...axisProps} allowDecimals={false} width={28} />
          <Tooltip
            labelFormatter={(v: string) => new Date(v).toLocaleString()}
            contentStyle={tipStyle}
          />
          <Line type="monotone" dataKey="signals" stroke={C.green} strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="decisions" stroke={C.cyan} strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="orders" stroke={C.amber} strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="risk_events" stroke={C.red} strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function CostBarChart({ data }: { data: CostPoint[] }) {
  return (
    <div className="chart-wrap-xl">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid stroke={C.border} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="agent" {...axisProps} />
          <YAxis {...axisProps} width={36} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
          <Tooltip contentStyle={tipStyle} formatter={(v: number) => [`$${v.toFixed(4)}`, "Cost"]} />
          <Bar dataKey="cost_usd" fill={C.cyan} radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function RiskBreakdownChart({ data }: { data: BreakdownPoint[] }) {
  return (
    <div className="chart-wrap-xl">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 8, bottom: 4, left: 4 }}>
          <CartesianGrid stroke={C.border} strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" {...axisProps} allowDecimals={false} />
          <YAxis
            dataKey="label"
            type="category"
            {...axisProps}
            width={90}
            tick={{ fontSize: 8, fontFamily: "'JetBrains Mono', monospace", fill: C.dim }}
            tickFormatter={(v: string) => (v.length > 14 ? `${v.slice(0, 14)}…` : v)}
          />
          <Tooltip contentStyle={tipStyle} />
          <Bar dataKey="count" fill={C.red} radius={[0, 2, 2, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function BreakdownMiniBar({ data, color }: { data: BreakdownPoint[]; color: string }) {
  const max = Math.max(...data.map(d => d.count), 1);
  return (
    <div className="space-y-1.5">
      {data.slice(0, 6).map(d => (
        <div key={d.label} className="flex items-center gap-2 font-mono text-[9px]">
          <span className="text-poly-muted w-20 truncate text-right" title={d.label}>{d.label}</span>
          <div className="flex-1 h-3 bg-poly-surface-container/40 relative">
            <div className="h-full" style={{ width: `${(d.count / max) * 100}%`, backgroundColor: color }} />
          </div>
          <span className="text-poly-dim w-6 text-right">{d.count}</span>
        </div>
      ))}
      {data.length === 0 && <div className="text-poly-dim font-mono text-[9px] text-center py-2">NO_DATA</div>}
    </div>
  );
}

export function DiscoveryFunnelChart({
  stages,
  operableCount,
}: {
  stages: FunnelStagePoint[];
  operableCount: number;
}) {
  const max = Math.max(...stages.map((stage) => stage.count), 1);
  const palette = ["#00f3ff", "#00ff41", "#fbbf24", "#ff8a3d", "#ff3131", "#a855f7"];

  return (
    <div className="h-full flex flex-col gap-2">
      <div className="space-y-2">
        {stages.map((stage, index) => {
          const width = Math.max(18, (stage.count / max) * 100);
          const color = stage.accent ?? palette[index % palette.length];
          return (
            <div key={stage.label} className="relative overflow-hidden border border-poly-border bg-poly-surface-dim/20">
              <div
                className="absolute inset-y-0 left-0 opacity-20"
                style={{ width: `${width}%`, backgroundColor: color }}
              />
              <div className="relative flex items-center justify-between gap-3 px-3 py-2">
                <div className="min-w-0">
                  <div className="font-mono text-[8px] uppercase text-poly-dim">{stage.label}</div>
                  {stage.detail && <div className="font-mono text-[9px] text-poly-muted truncate">{stage.detail}</div>}
                </div>
                <div className="text-right">
                  <div className="font-mono text-lg font-bold" style={{ color }}>
                    {stage.count}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
      <div className="border border-poly-border bg-poly-surface-dim/20 px-3 py-2 flex items-center justify-between font-mono text-[9px]">
        <div>
          <div className="text-poly-dim uppercase">Final_Operable</div>
          <div className="text-poly-muted">markets ready to enter the operation</div>
        </div>
        <div className={`px-2 py-1 font-bold ${operableCount > 0 ? "bg-poly-green text-poly-black" : "bg-poly-red text-white"}`}>
          {operableCount > 0 ? `OPERABLE ${operableCount}` : "NOT_OPERABLE"}
        </div>
      </div>
    </div>
  );
}

export function FlowAnalysisChart({ data }: { data: FlowPoint[] }) {
  return (
    <div className="chart-wrap-xl">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 8 }}>
          <CartesianGrid stroke={C.border} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="created_at"
            {...axisProps}
            tickFormatter={(v: string) => new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            minTickGap={30}
          />
          <YAxis
            yAxisId="notional"
            {...axisProps}
            width={44}
            tickFormatter={(v: number) => `$${v.toFixed(0)}`}
          />
          <YAxis
            yAxisId="dominance"
            orientation="right"
            {...axisProps}
            domain={[-1, 1]}
            width={44}
            tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
          />
          <Tooltip
            contentStyle={tipStyle}
            labelFormatter={(v: string) => new Date(v).toLocaleString()}
            formatter={(value: number, name: string) => {
              if (name === "dominance_score" || name === "confidence") {
                return [`${(value * 100).toFixed(1)}%`, name === "dominance_score" ? "Dominance" : "Confidence"];
              }
              return [`$${value.toFixed(2)}`, name === "up_notional" ? "Up Notional" : "Down Notional"];
            }}
          />
          <ReferenceLine yAxisId="dominance" y={0} stroke={C.dim} strokeDasharray="3 3" />
          <Bar yAxisId="notional" dataKey="up_notional" stackId="flow" fill={C.green} radius={[2, 2, 0, 0]} />
          <Bar yAxisId="notional" dataKey="down_notional" stackId="flow" fill={C.red} radius={[2, 2, 0, 0]} />
          <Line
            yAxisId="dominance"
            type="monotone"
            dataKey="dominance_score"
            stroke={C.cyan}
            strokeWidth={2}
            dot={false}
          />
          <Line yAxisId="dominance" type="monotone" dataKey="confidence" stroke={C.amber} strokeWidth={1.5} dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

