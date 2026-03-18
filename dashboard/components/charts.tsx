"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function OperationsCharts({
  costs,
  throughput,
  equity,
}: {
  costs: Array<{ agent: string; cost_usd: number }>;
  throughput: Array<{ label: string; value: number }>;
  equity: Array<{ created_at: string; total_equity: number; total_pnl: number }>;
}) {
  return (
    <>
      <article className="panel span-8">
        <h3>LLM Cost</h3>
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={costs}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(17,17,17,0.12)" />
              <XAxis dataKey="agent" stroke="#5c5a55" />
              <YAxis stroke="#5c5a55" />
              <Tooltip />
              <Bar dataKey="cost_usd" fill="#0f766e" radius={[0, 0, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </article>

      <article className="panel span-4">
        <h3>Pipeline Throughput</h3>
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={throughput}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(17,17,17,0.12)" />
              <XAxis dataKey="label" stroke="#5c5a55" />
              <YAxis stroke="#5c5a55" allowDecimals={false} />
              <Tooltip />
              <Line type="monotone" dataKey="value" stroke="#b45309" strokeWidth={3} dot={{ r: 4 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </article>

      <article className="panel span-12">
        <h3>Equity Curve</h3>
        <div className="chart-wrap chart-tall">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={equity}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(17,17,17,0.12)" />
              <XAxis
                dataKey="created_at"
                stroke="#5c5a55"
                tickFormatter={(value: string) => new Date(value).toLocaleTimeString()}
                minTickGap={32}
              />
              <YAxis stroke="#5c5a55" />
              <Tooltip
                labelFormatter={(value: string) => new Date(value).toLocaleString()}
                formatter={(value: number, name: string) => [`$${value.toFixed(2)}`, name]}
              />
              <Line type="monotone" dataKey="total_equity" stroke="#0f4c81" strokeWidth={3} dot={false} />
              <Line type="monotone" dataKey="total_pnl" stroke="#0f766e" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </article>
    </>
  );
}
