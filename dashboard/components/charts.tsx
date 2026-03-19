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
                  <stop offset="5%" stopColor="#31c48d" stopOpacity={0.42} />
                  <stop offset="95%" stopColor="#31c48d" stopOpacity={0.04} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="rgba(148, 163, 184, 0.16)" strokeDasharray="4 4" />
              <XAxis
                dataKey="created_at"
                stroke="#94a3b8"
                tickFormatter={(value: string) => new Date(value).toLocaleTimeString()}
                minTickGap={32}
              />
              <YAxis stroke="#94a3b8" />
              <Tooltip
                labelFormatter={(value: string) => new Date(value).toLocaleString()}
                formatter={(value: number, name: string) => [`$${value.toFixed(2)}`, name]}
                contentStyle={{
                  backgroundColor: "#08111f",
                  border: "1px solid rgba(49,196,141,0.25)",
                  borderRadius: 10,
                  color: "#e2e8f0",
                }}
              />
              <Legend />
              <Area type="monotone" dataKey="total_equity" stroke="#31c48d" fill="url(#equityFill)" strokeWidth={2.5} />
              <Line type="monotone" dataKey="total_pnl" stroke="#60a5fa" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="unrealized_pnl" stroke="#f59e0b" strokeWidth={2} dot={false} />
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
              <CartesianGrid stroke="rgba(148, 163, 184, 0.16)" strokeDasharray="4 4" />
              <XAxis dataKey="agent" stroke="#94a3b8" />
              <YAxis stroke="#94a3b8" />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#08111f",
                  border: "1px solid rgba(96,165,250,0.22)",
                  borderRadius: 10,
                  color: "#e2e8f0",
                }}
              />
              <Bar dataKey="cost_usd" fill="#60a5fa" radius={[6, 6, 0, 0]} />
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
              <CartesianGrid stroke="rgba(148, 163, 184, 0.16)" strokeDasharray="4 4" />
              <XAxis
                dataKey="bucket"
                stroke="#94a3b8"
                tickFormatter={(value: string) =>
                  new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                }
                minTickGap={26}
              />
              <YAxis stroke="#94a3b8" allowDecimals={false} />
              <Tooltip
                labelFormatter={(value: string) => new Date(value).toLocaleString()}
                contentStyle={{
                  backgroundColor: "#08111f",
                  border: "1px solid rgba(96,165,250,0.22)",
                  borderRadius: 10,
                  color: "#e2e8f0",
                }}
              />
              <Legend />
              <Line type="monotone" dataKey="signals" stroke="#31c48d" strokeWidth={2.5} dot={false} />
              <Line type="monotone" dataKey="decisions" stroke="#60a5fa" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="orders" stroke="#f59e0b" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="risk_events" stroke="#f87171" strokeWidth={2} dot={false} />
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
              <CartesianGrid stroke="rgba(148, 163, 184, 0.16)" strokeDasharray="4 4" />
              <XAxis type="number" stroke="#94a3b8" allowDecimals={false} />
              <YAxis
                dataKey="label"
                type="category"
                stroke="#94a3b8"
                width={138}
                tickFormatter={(value: string) => (value.length > 18 ? `${value.slice(0, 18)}...` : value)}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#08111f",
                  border: "1px solid rgba(248,113,113,0.25)",
                  borderRadius: 10,
                  color: "#e2e8f0",
                }}
              />
              <Bar dataKey="count" fill="#f87171" radius={[0, 6, 6, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </article>
    </>
  );
}
