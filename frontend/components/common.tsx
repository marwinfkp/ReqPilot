"use client";

import { AlertTriangle, CheckCircle2, CircleDashed, Info, Loader2, XCircle } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { SEVERITY_COLOR, generatedByLabel, label } from "@/lib/format";
import { cn } from "@/lib/utils";

export function PageHeader({ title, description, actions }: { title: string; description?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Section({ title, description, actions, children, className }: {
  title: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <Card className={cn("gap-4", className)}>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="space-y-1">
          <CardTitle className="text-base">{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
        {actions}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export function StatTile({ label: text, value, hint, icon: Icon, tone = "default" }: {
  label: string; value: React.ReactNode; hint?: React.ReactNode; icon?: LucideIcon; tone?: "default" | "warning" | "danger" | "good";
}) {
  const toneClass = { default: "text-primary bg-primary/10", warning: "text-amber-700 bg-amber-100", danger: "text-red-700 bg-red-100", good: "text-emerald-700 bg-emerald-100" }[tone];
  return (
    <Card className="gap-2 py-4">
      <CardContent className="flex items-start justify-between gap-3 px-4">
        <div className="min-w-0">
          <p className="text-xs font-medium text-muted-foreground">{text}</p>
          <p className="mt-1 truncate text-2xl font-semibold tabular-nums tracking-tight">{value}</p>
          {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
        </div>
        {Icon && (
          <span className={cn("rounded-lg p-2", toneClass)}>
            <Icon className="size-4" />
          </span>
        )}
      </CardContent>
    </Card>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border bg-background px-2 py-0.5 text-xs font-medium">
      <span className="size-2 rounded-full" style={{ background: SEVERITY_COLOR[severity] ?? "var(--series-neutral)" }} />
      {severity}
    </span>
  );
}

const STATUS_STYLES: Record<string, { cls: string; icon: LucideIcon }> = {
  completed: { cls: "bg-emerald-50 text-emerald-800 border-emerald-200", icon: CheckCircle2 },
  completed_with_warnings: { cls: "bg-amber-50 text-amber-900 border-amber-200", icon: AlertTriangle },
  running: { cls: "bg-blue-50 text-blue-800 border-blue-200", icon: Loader2 },
  queued: { cls: "bg-slate-50 text-slate-700 border-slate-200", icon: CircleDashed },
  failed: { cls: "bg-red-50 text-red-800 border-red-200", icon: XCircle },
  partial: { cls: "bg-amber-50 text-amber-900 border-amber-200", icon: AlertTriangle },
  skipped: { cls: "bg-slate-50 text-slate-700 border-slate-200", icon: CircleDashed },
  pass: { cls: "bg-emerald-50 text-emerald-800 border-emerald-200", icon: CheckCircle2 },
  warn: { cls: "bg-amber-50 text-amber-900 border-amber-200", icon: AlertTriangle },
  fail: { cls: "bg-red-50 text-red-800 border-red-200", icon: XCircle },
  info: { cls: "bg-blue-50 text-blue-800 border-blue-200", icon: Info },
  accepted: { cls: "bg-emerald-50 text-emerald-800 border-emerald-200", icon: CheckCircle2 },
  approved: { cls: "bg-emerald-50 text-emerald-800 border-emerald-200", icon: CheckCircle2 },
  modified: { cls: "bg-blue-50 text-blue-800 border-blue-200", icon: Info },
  overridden: { cls: "bg-blue-50 text-blue-800 border-blue-200", icon: Info },
  rejected: { cls: "bg-red-50 text-red-800 border-red-200", icon: XCircle },
  flagged: { cls: "bg-red-50 text-red-800 border-red-200", icon: AlertTriangle },
  changes_requested: { cls: "bg-amber-50 text-amber-900 border-amber-200", icon: AlertTriangle },
  pending: { cls: "bg-slate-50 text-slate-700 border-slate-200", icon: CircleDashed },
  pending_review: { cls: "bg-slate-50 text-slate-700 border-slate-200", icon: CircleDashed },
  pending_approval: { cls: "bg-slate-50 text-slate-700 border-slate-200", icon: CircleDashed },
  draft: { cls: "bg-slate-50 text-slate-700 border-slate-200", icon: CircleDashed },
  covered: { cls: "bg-emerald-50 text-emerald-800 border-emerald-200", icon: CheckCircle2 },
  partial_coverage: { cls: "bg-amber-50 text-amber-900 border-amber-200", icon: AlertTriangle },
  missing: { cls: "bg-red-50 text-red-800 border-red-200", icon: XCircle },
};

export function StatusPill({ status, text }: { status: string; text?: string }) {
  const style = STATUS_STYLES[status] ?? STATUS_STYLES.pending;
  const Icon = style.icon;
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium", style.cls)}>
      <Icon className={cn("size-3", status === "running" && "animate-spin")} />
      {text ?? label(status)}
    </span>
  );
}

export function Chips({ items, max = 6, mono = false }: { items: string[]; max?: number; mono?: boolean }) {
  if (!items?.length) return <span className="text-xs text-muted-foreground">–</span>;
  const shown = items.slice(0, max);
  return (
    <div className="flex flex-wrap gap-1">
      {shown.map((item) => (
        <Badge key={item} variant="secondary" className={cn("font-normal", mono && "font-mono text-[11px]")}>
          {mono ? item : label(item)}
        </Badge>
      ))}
      {items.length > max && <Badge variant="outline" className="font-normal">+{items.length - max}</Badge>}
    </div>
  );
}

export function ReqIds({ ids, max = 6 }: { ids: string[]; max?: number }) {
  return <Chips items={ids} max={max} mono />;
}

export function GeneratedBy({ value }: { value: string }) {
  const llm = value?.includes("openai");
  return (
    <span className={cn("inline-flex whitespace-nowrap rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
      llm ? "border-violet-200 bg-violet-50 text-violet-800" : "border-slate-200 bg-slate-50 text-slate-600")}>
      {generatedByLabel(value)}
    </span>
  );
}

export function AuthenticityBadge({ value }: { value: string }) {
  const styles: Record<string, string> = {
    official: "border-emerald-200 bg-emerald-50 text-emerald-800",
    illustrative: "border-amber-200 bg-amber-50 text-amber-900",
    reference: "border-sky-200 bg-sky-50 text-sky-800",
  };
  const titles: Record<string, string> = {
    official: "Official document uploaded by an authorised user",
    illustrative: "Fictional demonstration policy - not a law or regulation",
    reference: "Document-level reference summary - not regulatory text; requires verification",
  };
  return (
    <span title={titles[value]} className={cn("inline-flex rounded border px-1.5 py-0.5 text-[11px] font-medium", styles[value] ?? styles.reference)}>
      {label(value)}
    </span>
  );
}

export function EmptyState({ icon: Icon = Info, title, description, action }: {
  icon?: LucideIcon; title: string; description?: React.ReactNode; action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed px-6 py-12 text-center">
      <span className="rounded-full bg-muted p-3"><Icon className="size-5 text-muted-foreground" /></span>
      <p className="mt-3 font-medium">{title}</p>
      {description && <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function LoadingState({ text = "Loading…" }: { text?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" /> {text}
    </div>
  );
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-red-200 bg-red-50 px-6 py-10 text-center text-sm text-red-800">
      <XCircle className="size-5" />
      <p>{message}</p>
      {retry && <Button variant="outline" size="sm" onClick={retry}>Try again</Button>}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warning" | "danger"; children: React.ReactNode }) {
  const cls = {
    info: "border-blue-200 bg-blue-50 text-blue-900",
    warning: "border-amber-200 bg-amber-50 text-amber-950",
    danger: "border-red-200 bg-red-50 text-red-900",
  }[tone];
  const Icon = tone === "info" ? Info : AlertTriangle;
  return (
    <div className={cn("flex gap-2 rounded-lg border px-4 py-3 text-sm", cls)}>
      <Icon className="mt-0.5 size-4 shrink-0" />
      <div>{children}</div>
    </div>
  );
}

export function BulletList({ items, empty = "None" }: { items: string[]; empty?: string }) {
  if (!items?.length) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm">
      {items.map((item, i) => <li key={i}>{item}</li>)}
    </ul>
  );
}
