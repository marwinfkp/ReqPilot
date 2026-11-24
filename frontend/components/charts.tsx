"use client";

// Charts follow the data-viz rules: one hue per series in fixed palette order,
// thin bars with rounded data-ends, recessive grid, hover tooltips, legends for
// >= 2 series, and status colours only together with a text label.
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis, LabelList,
} from "recharts";

import { SEVERITY_COLOR } from "@/lib/format";
import { cn } from "@/lib/utils";

const AXIS = { fontSize: 11, fill: "#52514e" };
const tooltipStyle = { fontSize: 12, borderRadius: 8, border: "1px solid #e2e8f0" };

/** Single-series horizontal bars, e.g. SDLC suitability scores. */
export function HorizontalBars({ data, max, highlight, height, unit = "" }: {
  data: { name: string; value: number }[]; max?: number; highlight?: string; height?: number; unit?: string;
}) {
  return (
    <ResponsiveContainer width="100%" height={height ?? Math.max(160, data.length * 38)}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 48, top: 4, bottom: 4 }} barCategoryGap={8}>
        <CartesianGrid horizontal={false} stroke="#eef0f3" />
        <XAxis type="number" domain={[0, max ?? "auto"]} tick={AXIS} axisLine={false} tickLine={false} />
        <YAxis type="category" dataKey="name" width={190} tick={AXIS} axisLine={false} tickLine={false} />
        <Tooltip cursor={{ fill: "rgba(42,120,214,0.06)" }} contentStyle={tooltipStyle} formatter={(v: number) => [`${v}${unit}`, "Value"]} />
        <Bar dataKey="value" radius={[0, 4, 4, 0]} maxBarSize={18}>
          {data.map((d) => (
            <Cell key={d.name} fill={highlight && d.name !== highlight ? "#86b6ef" : "var(--series-1)"} />
          ))}
          <LabelList dataKey="value" position="right" style={{ fontSize: 11, fill: "#0b0b0b" }} formatter={(v: number) => `${v}${unit}`} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Single-series vertical bars, e.g. requirements per category. */
export function ColumnBars({ data, height = 240 }: { data: { name: string; value: number }[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ left: -16, right: 8, top: 16, bottom: 40 }}>
        <CartesianGrid vertical={false} stroke="#eef0f3" />
        <XAxis dataKey="name" tick={{ ...AXIS, fontSize: 10 }} angle={-35} textAnchor="end" interval={0} axisLine={false} tickLine={false} />
        <YAxis allowDecimals={false} tick={AXIS} axisLine={false} tickLine={false} />
        <Tooltip cursor={{ fill: "rgba(42,120,214,0.06)" }} contentStyle={tooltipStyle} />
        <Bar dataKey="value" name="Requirements" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={28} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Two-series stacked bars with legend, e.g. mapped vs unmapped requirements. */
export function StackedBars({ data, keys, height = 240 }: {
  data: Record<string, string | number>[]; keys: { key: string; label: string; color: string }[]; height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ left: -16, right: 8, top: 8, bottom: 8 }}>
        <CartesianGrid vertical={false} stroke="#eef0f3" />
        <XAxis dataKey="name" tick={AXIS} axisLine={false} tickLine={false} />
        <YAxis allowDecimals={false} tick={AXIS} axisLine={false} tickLine={false} />
        <Tooltip cursor={{ fill: "rgba(42,120,214,0.06)" }} contentStyle={tooltipStyle} />
        <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" />
        {keys.map((k, i) => (
          <Bar key={k.key} dataKey={k.key} name={k.label} stackId="a" fill={k.color} maxBarSize={36}
            stroke="#ffffff" strokeWidth={1} radius={i === keys.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Severity distribution; status colours are always accompanied by the severity label on the axis. */
export function SeverityBars({ counts, height = 200 }: { counts: Record<string, number>; height?: number }) {
  const data = ["Critical", "High", "Medium", "Low"].map((s) => ({ name: s, value: counts[s] ?? 0 }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ left: -16, right: 8, top: 16, bottom: 4 }}>
        <CartesianGrid vertical={false} stroke="#eef0f3" />
        <XAxis dataKey="name" tick={AXIS} axisLine={false} tickLine={false} />
        <YAxis allowDecimals={false} tick={AXIS} axisLine={false} tickLine={false} />
        <Tooltip cursor={{ fill: "rgba(42,120,214,0.06)" }} contentStyle={tooltipStyle} />
        <Bar dataKey="value" name="Count" radius={[4, 4, 0, 0]} maxBarSize={44}>
          {data.map((d) => <Cell key={d.name} fill={SEVERITY_COLOR[d.name]} />)}
          <LabelList dataKey="value" position="top" style={{ fontSize: 11, fill: "#0b0b0b" }} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** 5x5 risk matrix: likelihood (rows, 5 at top) x impact (columns). Cell colour = severity band of L x I. */
export function RiskMatrix({ risks }: { risks: { risk_id: string; likelihood: number; impact: number; description: string }[] }) {
  const band = (score: number) => (score >= 16 ? "Critical" : score >= 10 ? "High" : score >= 5 ? "Medium" : "Low");
  return (
    <div className="flex gap-2">
      <div className="flex w-6 items-center justify-center">
        <span className="-rotate-90 whitespace-nowrap text-xs text-muted-foreground">Likelihood →</span>
      </div>
      <div className="flex-1">
        <div className="grid grid-cols-5 gap-1">
          {[5, 4, 3, 2, 1].flatMap((l) =>
            [1, 2, 3, 4, 5].map((i) => {
              const here = risks.filter((r) => r.likelihood === l && r.impact === i);
              const severity = band(l * i);
              return (
                <div key={`${l}-${i}`} title={`Likelihood ${l} × Impact ${i} = ${l * i} (${severity})\n${here.map((r) => `${r.risk_id}: ${r.description}`).join("\n")}`}
                  className={cn("relative flex h-14 flex-col items-center justify-center rounded-md border text-xs", here.length && "ring-2 ring-foreground/70")}
                  style={{ background: `color-mix(in srgb, ${SEVERITY_COLOR[severity]} ${here.length ? 55 : 22}%, white)` }}>
                  <span className="font-semibold tabular-nums">{here.length || ""}</span>
                  {here.length > 0 && <span className="text-[10px]">{here.slice(0, 2).map((r) => r.risk_id.replace("RISK-", "R")).join(", ")}</span>}
                </div>
              );
            }),
          )}
        </div>
        <div className="mt-1 grid grid-cols-5 text-center text-[11px] text-muted-foreground">
          {[1, 2, 3, 4, 5].map((i) => <span key={i}>{i}</span>)}
        </div>
        <p className="text-center text-xs text-muted-foreground">Impact →</p>
        <div className="mt-2 flex flex-wrap justify-center gap-3 text-xs">
          {["Low", "Medium", "High", "Critical"].map((s) => (
            <span key={s} className="inline-flex items-center gap-1">
              <span className="size-2.5 rounded-sm" style={{ background: SEVERITY_COLOR[s] }} /> {s}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Thin horizontal meter (0..1) with its value printed beside it. */
export function Meter({ value, className }: { value: number; className?: string }) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="h-1.5 flex-1 rounded-full bg-muted">
        <div className="h-1.5 rounded-full bg-[var(--series-1)]" style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
      </div>
      <span className="w-10 text-right text-xs tabular-nums text-muted-foreground">{Math.round(value * 100)}%</span>
    </div>
  );
}
