"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type CostPoint = { agent: string; cost_usd: number; calls: number };
type PipelinePoint = { bucket: string; signals: number; decisions: number; orders: number; risk_events: number };
type EquityPoint = { created_at: string; total_equity: number; total_pnl: number; unrealized_pnl: number };
type BreakdownPoint = { label: string; count: number };

type Props = {
  costs: CostPoint[];
  pipeline: PipelinePoint[];
  equity: EquityPoint[];
  riskBreakdown: BreakdownPoint[];
};

export function OperationsCharts({ costs, pipeline, equity, riskBreakdown }: Props) {
  // Theme colors from CSS variables
  const colors = {
    green: "#4ade80",
    cyan: "#38bdf8",
    amber: "#fbbf24",
    red: "#f87171",
    slate: "#94a3b8",
    bg: "#02040a",
    line: "rgba(56, 189, 248, 0.15)",
  };

  const tooltipStyle = {
    backgroundColor: "rgba(2, 4, 10, 0.95)",
    border: `1px solid ${colors.line}`,
    borderRadius: "4px",
    color: "#e0f2fe",
    fontFamily: "var(--font-mono), monospace",
    fontSize: "0.85rem",
  };

  return (
    <>
      <article className="panel chart-panel span-8">
        <div className="panel-head">
          <h3>Equity Curve</h3>
          <span className="terminal-pill">mark-to-market</span>
        </div>
        <div className="chart-wrap chart-xl">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={equity}>
              <defs>
                <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={colors.green} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={colors.green} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke={colors.line} strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="created_at"
                stroke={colors.slate}
                tick={{ fontSize: 10 }}
                tickFormatter={(value: string) => new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                minTickGap={32}
              />
              <YAxis stroke={colors.slate} tick={{ fontSize: 10 }} />
              <Tooltip
                labelFormatter={(value: string) => new Date(value).toLocaleString()}
                formatter={(value: number, name: string) => [`$${value.toFixed(2)}`, name]}
                contentStyle={tooltipStyle}
              />
              <Legend iconType="circle" wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }} />
              <Area type="monotone" dataKey="total_equity" stroke={colors.green} fill="url(#equityFill)" strokeWidth={2} />
              <Line type="monotone" dataKey="total_pnl" stroke={colors.cyan} strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="unrealized_pnl" stroke={colors.amber} strokeWidth={2} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </article>

      <article className="panel chart-panel span-4">
        <div className="panel-head">
          <h3>LLM Cost</h3>
          <span className="terminal-pill">daily</span>
        </div>
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={costs}>
              <CartesianGrid stroke={colors.line} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="agent" stroke={colors.slate} tick={{ fontSize: 10 }} />
              <YAxis stroke={colors.slate} tick={{ fontSize: 10 }} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value: number) => [`$${value.toFixed(4)}`, "Cost"]}
              />
              <Bar dataKey="cost_usd" fill={colors.cyan} radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </article>

      <article className="panel chart-panel span-7">
        <div className="panel-head">
          <h3>Pipeline Rhythm</h3>
          <span className="terminal-pill">hour buckets</span>
        </div>
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={pipeline}>
              <CartesianGrid stroke={colors.line} strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="bucket"
                stroke={colors.slate}
                tick={{ fontSize: 10 }}
                tickFormatter={(value: string) =>
                  new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                }
                minTickGap={26}
              />
              <YAxis stroke={colors.slate} tick={{ fontSize: 10 }} allowDecimals={false} />
              <Tooltip
                labelFormatter={(value: string) => new Date(value).toLocaleString()}
                contentStyle={tooltipStyle}
              />
              <Legend iconType="circle" wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }} />
              <Line type="monotone" dataKey="signals" stroke={colors.green} strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="decisions" stroke={colors.cyan} strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="orders" stroke={colors.amber} strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="risk_events" stroke={colors.red} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </article>

      <article className="panel chart-panel span-5">
        <div className="panel-head">
          <h3>Risk Breakdown</h3>
          <span className="terminal-pill">top blockers</span>
        </div>
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={riskBreakdown} layout="vertical" margin={{ left: 12, right: 8 }}>
              <CartesianGrid stroke={colors.line} strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" stroke={colors.slate} tick={{ fontSize: 10 }} allowDecimals={false} />
              <YAxis
                dataKey="label"
                type="category"
                stroke={colors.slate}
                width={120}
                tick={{ fontSize: 9 }}
                tickFormatter={(value: string) => (value.length > 15 ? `${value.slice(0, 15)}...` : value)}
              />
              <Tooltip contentStyle={tooltipStyle} />
              <Bar dataKey="count" fill={colors.red} radius={[0, 2, 2, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </article>
    </>
  );
}

